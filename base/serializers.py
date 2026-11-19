"""
Input validation (DRF serializers) + output builders.

Output JSON deliberately matches what the mobile app's TypeScript models expect
(ids as strings, `task_assignees[].profiles`, `photo_url`, ...), so screens stay simple.
"""
import zlib

from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from rest_framework import serializers

from .models import Profile


# ── Output builders ───────────────────────────────────────────────────────────

def _iso(v):
    return v.isoformat() if v else None


def photo_url(profile, request):
    if not profile.photo:
        return None
    version = zlib.crc32(profile.photo.name.encode())
    return request.build_absolute_uri(reverse('avatar', args=[profile.user_id])) + f'?v={version}'


def full_name(user):
    return (user.get_full_name() or '').strip()


def public_profile(user, request):
    p = user.profile
    return {'id': str(user.id), 'full_name': full_name(user), 'photo_url': photo_url(p, request), 'job_title': p.job_title or None}


def profile_payload(user, request, private=True):
    p = user.profile
    out = {
        'id': str(user.id), 'email': user.email, 'full_name': full_name(user), 'photo_url': photo_url(p, request),
        'phone': p.phone or None, 'department': p.department or None, 'job_title': p.job_title or None,
        'staff_id': p.staff_id, 'start_date': _iso(p.start_date), 'role': 'admin' if p.is_admin else 'staff',
        'stage': p.stage, 'approved': p.approved, 'profile_complete': p.profile_complete,
        'email_verified': p.email_verified, 'weekly_hours_target': float(p.weekly_hours_target),
        'push_enabled': bool(p.push_token), 'email_reminders': p.email_reminders, 'created_at': _iso(p.created_at),
    }
    if private:
        out.update({
            'date_of_birth': _iso(p.date_of_birth), 'gender': p.gender or None, 'national_id': p.national_id or None,
            'address': p.address or None, 'emergency_name': p.emergency_name or None,
            'emergency_phone': p.emergency_phone or None, 'emergency_relation': p.emergency_relation or None,
        })
    return out


def assignment_payload(a, request):
    return {
        'task_id': str(a.task_id), 'user_id': str(a.user_id), 'status': a.status,
        'started_at': _iso(a.started_at), 'timer_started_at': _iso(a.timer_started_at),
        'actual_seconds': a.actual_seconds, 'completed_at': _iso(a.completed_at), 'is_late': a.is_late,
        'profiles': public_profile(a.user, request),
    }


def task_payload(t, request):
    return {
        'id': str(t.id), 'title': t.title, 'description': t.description or None, 'priority': t.priority,
        'deadline': _iso(t.deadline), 'estimated_minutes': t.estimated_minutes,
        'created_by': str(t.created_by_id) if t.created_by_id else None, 'created_at': _iso(t.created_at),
        'task_assignees': [assignment_payload(a, request) for a in t.assignments.all()],
    }


def diary_payload(d):
    return {
        'id': str(d.id), 'user_id': str(d.user_id), 'entry_date': d.entry_date.isoformat(), 'content': d.content,
        'mood': d.mood or None, 'hours_worked': float(d.hours_worked), 'admin_comment': d.admin_comment or None,
        'created_at': _iso(d.created_at),
    }


def event_payload(e):
    return {
        'id': str(e.id), 'title': e.title, 'description': e.description or None, 'kind': e.kind,
        'starts_at': _iso(e.starts_at), 'ends_at': _iso(e.ends_at), 'is_company': e.is_company,
        'created_by': str(e.created_by_id) if e.created_by_id else None,
    }


def document_payload(d):
    return {
        'id': str(d.id), 'kind': d.kind, 'title': d.title, 'content': d.content, 'shared': d.shared,
        'created_by': str(d.created_by_id), 'created_at': _iso(d.created_at), 'updated_at': _iso(d.updated_at),
    }


def file_payload(f):
    return {
        'id': str(f.id), 'owner_id': str(f.owner_id), 'name': f.name, 'mime': f.mime or None, 'size': f.size,
        'category': f.category, 'shared': f.shared, 'created_at': _iso(f.created_at),
        'profiles': {'full_name': full_name(f.owner)},
    }


def notification_payload(n):
    return {
        'id': str(n.id), 'user_id': str(n.user_id), 'title': n.title, 'body': n.description or None, 'kind': n.kind,
        'ref_id': n.ref_id or None, 'read': n.is_read, 'created_at': _iso(n.created_at),
    }


def approval_payload(a):
    return {'id': str(a.id), 'member_id': str(a.member_id), 'stage_from': a.stage_from or None,
            'stage_to': a.stage_to, 'note': a.note or None, 'created_at': _iso(a.created_at)}


# ── Input serializers ─────────────────────────────────────────────────────────

def _validate_password(value, user=None):
    try:
        password_validation.validate_password(value, user)
    except DjangoValidationError as e:
        raise serializers.ValidationError(list(e.messages))
    return value


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(min_length=2, max_length=120)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_password(self, value):
        return _validate_password(value)

    def create(self, data):
        parts = data['full_name'].strip().split()
        return User.objects.create_user(
            username=data['email'], email=data['email'], password=data['password'],
            first_name=parts[0], last_name=' '.join(parts[1:]))


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class CodeSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(min_length=6, max_length=6)


class ResetPasswordSerializer(CodeSerializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        return _validate_password(value)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=False, allow_blank=True, default='')
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        return _validate_password(value, self.context.get('user'))


class ProfileInputSerializer(serializers.Serializer):
    """Registration form. Completeness is checked in `missing_profile_fields`."""
    full_name = serializers.CharField(min_length=2, max_length=120, required=False)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    department = serializers.CharField(max_length=80, required=False, allow_blank=True)
    job_title = serializers.CharField(max_length=80, required=False, allow_blank=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.CharField(max_length=20, required=False, allow_blank=True)
    national_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    emergency_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    emergency_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    emergency_relation = serializers.CharField(max_length=50, required=False, allow_blank=True)
    email_reminders = serializers.BooleanField(required=False)
    photo = serializers.ImageField(required=False)

    def validate_photo(self, f):
        if f.size > 8 * 1024 * 1024:
            raise serializers.ValidationError('Photo must be under 8 MB.')
        return f


def missing_profile_fields(p):
    """Human-readable list of required registration fields still empty (empty list = complete)."""
    checks = [
        ('a profile photo', p.photo), ('full name', (p.user.get_full_name() or '').strip()), ('phone number', len(p.phone) >= 7),
        ('job title', p.job_title), ('date of birth', p.date_of_birth), ('gender', p.gender),
        ('national ID', len(p.national_id) >= 4), ('home address', len(p.address) >= 3),
        ('emergency contact', p.emergency_name and len(p.emergency_phone) >= 7),
    ]
    return [label for label, ok in checks if not ok]


class MemberApproveSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Profile.STAGES)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class TaskInputSerializer(serializers.Serializer):
    title = serializers.CharField(min_length=3, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    priority = serializers.ChoiceField(choices=['low', 'medium', 'high'], default='medium')
    deadline = serializers.DateTimeField()
    estimated_minutes = serializers.IntegerField(min_value=5, max_value=60 * 24 * 14)
    assignee_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=200)


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['todo', 'in_progress', 'done'])


class DiaryInputSerializer(serializers.Serializer):
    entry_date = serializers.DateField()
    content = serializers.CharField(min_length=3)
    mood = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=10)
    hours_worked = serializers.DecimalField(max_digits=4, decimal_places=1, min_value=0, max_value=24, required=False, default=0)


class EventInputSerializer(serializers.Serializer):
    title = serializers.CharField(min_length=2, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    kind = serializers.ChoiceField(choices=['event', 'meeting', 'deadline'], default='event')
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField(required=False, allow_null=True)
    is_company = serializers.BooleanField(required=False, default=False)


class DocumentInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=['meeting', 'report', 'event'], default='report')
    title = serializers.CharField(min_length=2, max_length=200)
    content = serializers.CharField(required=False, allow_blank=True, default='')
    shared = serializers.BooleanField(required=False, default=False)
