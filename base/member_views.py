from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ApprovalRecord
from .permissions import IsAdmin
from .serializers import MemberApproveSerializer, approval_payload, profile_payload
from .services import audit, notify
from .stats import week_stats
from .views_util import parse_range

STAGE_TEXT = {'pending': 'Pending approval', 'onboarding': 'Onboarding', 'active': 'Active', 'confirmed': 'Confirmed'}


def _staff():
    return User.objects.filter(is_active=True, profile__role='staff').select_related('profile').order_by('-date_joined')


class MemberListView(APIView):
    """Admin: everyone registered (without private details — those are on the detail endpoint)."""
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([profile_payload(u, request, private=False) for u in _staff()])


class MemberDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, user_id):
        return Response(profile_payload(get_object_or_404(_staff(), pk=user_id), request))


class MemberApproveView(APIView):
    """Move a member to a stage (Pending → Onboarding → Active → Confirmed) with a note; keeps an audit trail."""
    permission_classes = [IsAdmin]

    def post(self, request, user_id):
        user = get_object_or_404(_staff(), pk=user_id)
        ser = MemberApproveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        stage, note = ser.validated_data['stage'], ser.validated_data.get('note', '').strip()
        ApprovalRecord.objects.create(member=user, stage_from=user.profile.stage, stage_to=stage, note=note, approved_by=request.user)
        user.profile.stage = stage
        user.profile.save(update_fields=['stage', 'updated_at'])
        notify(user, 'Progress update', f'You are now: {STAGE_TEXT[stage]}.' + (f' {note}' if note else ''), 'approval')
        audit(request, 'member_stage_changed', member=user.id, stage=stage)
        return Response(profile_payload(user, request))


class MemberApprovalsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, user_id):
        return Response([approval_payload(a) for a in ApprovalRecord.objects.filter(member_id=user_id)])


class MyApprovalsView(APIView):
    def get(self, request):
        return Response([approval_payload(a) for a in ApprovalRecord.objects.filter(member=request.user)])


class MemberStatsView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, user_id):
        user = get_object_or_404(_staff(), pk=user_id)
        start, end = parse_range(request)
        return Response(week_stats(user, start, end, user.profile.weekly_hours_target))
