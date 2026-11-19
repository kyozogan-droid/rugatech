import mimetypes

import requests
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import email as mail
from .models import EmailCode, Profile
from .serializers import (ChangePasswordSerializer, CodeSerializer, LoginSerializer, ProfileInputSerializer,
                          RegisterSerializer, ResetPasswordSerializer, missing_profile_fields, profile_payload)
from .services import audit, client_ip, notify_admins


def login_payload(request, user, **extra):
    """Same envelope as SRC's LoginView: {token, user, message}."""
    token, _ = Token.objects.get_or_create(user=user)
    user.last_login = timezone.now()
    user.save(update_fields=['last_login'])
    return {'token': token.key, 'user': profile_payload(user, request), 'message': 'Login successful', **extra}


def _user_by_email(email):
    return User.objects.filter(email__iexact=str(email).strip(), is_active=True).select_related('profile').first()


class PublicView(APIView):
    """Public endpoints ignore any (possibly stale) Authorization header instead of failing with 401."""
    authentication_classes = []
    permission_classes = [AllowAny]


# ── Register / verify / login ─────────────────────────────────────────────────

class RegisterView(PublicView):
    throttle_scope = 'auth'

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        audit(request, 'register', user=user)
        if settings.REQUIRE_EMAIL_VERIFICATION:
            mail.send_verification_code(user, EmailCode.issue(user, 'verify'))
            return Response({'needs_verification': True, 'email': user.email,
                             'message': 'We sent a 6-digit code to your email.'}, status=status.HTTP_201_CREATED)
        Profile.objects.filter(pk=user.profile.pk).update(email_verified=True)
        user.profile.refresh_from_db()
        notify_admins('New staff member', f'{user.get_full_name() or user.email} registered and is awaiting your approval.', 'info')
        return Response(login_payload(request, user, is_new_user=True), status=status.HTTP_201_CREATED)


class VerifyEmailView(PublicView):
    throttle_scope = 'code'

    def post(self, request):
        ser = CodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = _user_by_email(ser.validated_data['email'])
        if not user or not EmailCode.verify(user, 'verify', ser.validated_data['code']):
            return Response({'error': 'That code is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        Profile.objects.filter(pk=user.profile.pk).update(email_verified=True)
        user.profile.refresh_from_db()
        audit(request, 'email_verified', user=user)
        notify_admins('New staff member', f'{user.get_full_name() or user.email} registered and is awaiting your approval.', 'info')
        return Response(login_payload(request, user, is_new_user=True))


class ResendCodeView(PublicView):
    throttle_scope = 'code'

    def post(self, request):
        user = _user_by_email(request.data.get('email', ''))
        if user and not user.profile.email_verified:
            mail.send_verification_code(user, EmailCode.issue(user, 'verify'))
        return Response({'message': 'If that account needs verification, a new code is on its way.'})


class LoginView(PublicView):
    throttle_scope = 'auth'

    def post(self, request):
        ip = client_ip(request) or 'unknown'
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data['email'].strip().lower()
        cache_key = f'login_fail_{ip}_{email}'
        if cache.get(cache_key, 0) >= 8:
            return Response({'error': 'Too many failed attempts. Please try again in 30 minutes.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        match = _user_by_email(email)
        user = authenticate(request, username=match.username, password=ser.validated_data['password']) if match else None
        if not user:
            cache.set(cache_key, cache.get(cache_key, 0) + 1, timeout=1800)
            audit(request, 'failed_login', email=email)
            return Response({'error': 'Incorrect email or password.'}, status=status.HTTP_400_BAD_REQUEST)

        if settings.REQUIRE_EMAIL_VERIFICATION and not user.profile.email_verified:
            mail.send_verification_code(user, EmailCode.issue(user, 'verify'))
            return Response({'error': 'Please verify your email address before signing in. We sent you a new code.',
                             'needs_verification': True, 'email': user.email}, status=status.HTTP_403_FORBIDDEN)

        cache.delete(cache_key)
        audit(request, 'login', user=user, method='password')
        return Response(login_payload(request, user))


class LogoutView(APIView):
    def post(self, request):
        audit(request, 'logout')
        Token.objects.filter(user=request.user).delete()
        return Response({'message': 'Logged out'})


# ── Google sign-in (same request shape as SRC: POST {access_token}) ───────────

class GoogleLoginView(PublicView):
    throttle_scope = 'auth'

    def post(self, request):
        allowed = settings.GOOGLE_OAUTH_CLIENT_IDS
        if not allowed:
            return Response({'error': 'Google sign-in is not configured on the server.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        id_token, access_token = request.data.get('id_token'), request.data.get('access_token')
        if not (id_token or access_token):
            return Response({'error': 'access_token or id_token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            info = self._verify(id_token, access_token, allowed)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except requests.RequestException:
            return Response({'error': 'Could not reach Google. Try again.'}, status=status.HTTP_502_BAD_GATEWAY)

        email = (info.get('email') or '').lower()
        user = _user_by_email(email)
        created = False
        if not user:
            first = info.get('given_name') or (info.get('name') or '').split(' ')[0]
            last = info.get('family_name') or ' '.join((info.get('name') or '').split(' ')[1:])
            user = User.objects.create_user(username=email, email=email, password=None, first_name=first or '', last_name=last or '')
            user = User.objects.select_related('profile').get(pk=user.pk)
            created = True
        if not user.profile.email_verified:
            Profile.objects.filter(pk=user.profile.pk).update(email_verified=True)
            user.profile.refresh_from_db()
        audit(request, 'google_login', user=user, created=created)
        if created:
            notify_admins('New staff member', f'{user.get_full_name() or user.email} signed up with Google and is awaiting your approval.', 'info')
        return Response(login_payload(request, user, is_new_user=created, message='Google login successful'))

    @staticmethod
    def _verify(id_token, access_token, allowed):
        """Verify the token was issued by Google FOR OUR APP (audience check) — never trust a bare access token."""
        if id_token:
            r = requests.get('https://oauth2.googleapis.com/tokeninfo', params={'id_token': id_token}, timeout=10)
            if r.status_code != 200:
                raise ValueError('Invalid or expired Google token.')
            info = r.json()
            aud = info.get('aud')
        else:
            r = requests.get('https://oauth2.googleapis.com/tokeninfo', params={'access_token': access_token}, timeout=10)
            if r.status_code != 200:
                raise ValueError('Invalid or expired Google token.')
            info = r.json()
            aud = info.get('aud') or info.get('azp')
            u = requests.get('https://www.googleapis.com/oauth2/v3/userinfo', headers={'Authorization': f'Bearer {access_token}'}, timeout=10)
            if u.status_code == 200:
                info = {**u.json(), **{k: v for k, v in info.items() if k in ('email', 'email_verified')}}
        if aud not in allowed:
            raise ValueError('This Google token was not issued for the Rugatech app.')
        if str(info.get('email_verified')).lower() != 'true' or not info.get('email'):
            raise ValueError('Your Google email is not verified.')
        return info


# ── Password reset (6-digit code by email — no deep links needed in the app) ──

class ForgotPasswordView(PublicView):
    throttle_scope = 'code'

    def post(self, request):
        user = _user_by_email(request.data.get('email', ''))
        if user:
            mail.send_password_reset_code(user, EmailCode.issue(user, 'reset'))
            audit(request, 'password_reset_requested', user=user)
        # Same answer either way: never reveal whether an email is registered.
        return Response({'message': 'If an account exists for that email, a reset code is on its way.'})


class ResetPasswordView(PublicView):
    throttle_scope = 'code'

    def post(self, request):
        ser = ResetPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user = _user_by_email(d['email'])
        if not user or not EmailCode.verify(user, 'reset', d['code']):
            return Response({'error': 'That code is invalid or has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(d['password'])
        user.save(update_fields=['password'])
        Profile.objects.filter(pk=user.profile.pk).update(email_verified=True)
        Token.objects.filter(user=user).delete()          # sign out everywhere
        audit(request, 'password_reset_completed', user=user)
        return Response({'message': 'Password updated. You can sign in now.'})


class ChangePasswordView(APIView):
    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={'user': request.user})
        ser.is_valid(raise_exception=True)
        user = request.user
        if user.has_usable_password() and not user.check_password(ser.validated_data.get('current_password', '')):
            return Response({'error': 'Your current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(ser.validated_data['new_password'])
        user.save(update_fields=['password'])
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        audit(request, 'password_changed')
        return Response({'message': 'Password updated.', 'token': token.key})


# ── Me / profile ──────────────────────────────────────────────────────────────

class MeView(APIView):
    """GET → my full profile. PATCH → app settings (email reminders)."""

    def get(self, request):
        return Response(profile_payload(request.user, request))

    def patch(self, request):
        if 'email_reminders' in request.data:
            p = request.user.profile
            p.email_reminders = str(request.data['email_reminders']).lower() in ('1', 'true', 'yes')
            p.save(update_fields=['email_reminders', 'updated_at'])
        return Response(profile_payload(request.user, request))


class ProfileView(APIView):
    """Registration form (multipart so the photo uploads in the same request)."""
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def put(self, request):
        return self._save(request)

    patch = put

    def _save(self, request):
        ser = ProfileInputSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        d, user, p = ser.validated_data, request.user, request.user.profile

        if 'full_name' in d:
            parts = d['full_name'].strip().split()
            user.first_name, user.last_name = parts[0], ' '.join(parts[1:])
            user.save(update_fields=['first_name', 'last_name'])
        for field in ('phone', 'department', 'job_title', 'start_date', 'date_of_birth', 'gender', 'national_id',
                      'address', 'emergency_name', 'emergency_phone', 'emergency_relation', 'email_reminders'):
            if field in d:
                setattr(p, field, d[field].strip() if isinstance(d[field], str) else d[field])
        if 'photo' in d:
            if p.photo:
                p.photo.delete(save=False)
            p.photo = d['photo']

        missing = missing_profile_fields(p)
        was_complete = p.profile_complete
        p.profile_complete = not missing
        p.save()
        if p.profile_complete and not was_complete and not p.is_admin:
            notify_admins('Registration completed', f'{user.get_full_name() or user.email} finished their profile and can be approved.', 'info')
        audit(request, 'profile_update')
        out = profile_payload(user, request)
        out['missing_fields'] = missing
        return Response(out)


class PushTokenView(APIView):
    def post(self, request):
        token = str(request.data.get('token', ''))[:200]
        p = request.user.profile
        p.push_token = token
        p.save(update_fields=['push_token', 'updated_at'])
        return Response({'ok': True})


class DeleteAccountView(APIView):
    """In-app account deletion (required by Google Play / App Store for apps with sign-up)."""

    def post(self, request):
        user = request.user
        if user.has_usable_password() and not user.check_password(request.data.get('password', '')):
            return Response({'error': 'Your password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        if user.profile.is_admin and not User.objects.filter(profile__role='admin', is_active=True).exclude(pk=user.pk).exists():
            return Response({'error': 'You are the only admin. Make someone else an admin before deleting this account.'}, status=status.HTTP_400_BAD_REQUEST)
        audit(request, 'account_deleted', email=user.email)
        user.delete()
        return Response({'message': 'Account deleted.'})


# ── Avatars: public images, served through the API so any storage backend works ──

class AvatarView(PublicView):
    def get(self, request, user_id):
        p = Profile.objects.filter(user_id=user_id).exclude(photo='').first()
        if not p or not p.photo:
            raise Http404
        try:
            fh = default_storage.open(p.photo.name, 'rb')
        except Exception:
            raise Http404
        resp = FileResponse(fh, content_type=mimetypes.guess_type(p.photo.name)[0] or 'image/jpeg')
        resp['Cache-Control'] = 'public, max-age=86400'
        return resp
