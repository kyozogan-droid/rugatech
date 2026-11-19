from datetime import timedelta

from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DiaryEntry, TimeLog
from .permissions import IsAdmin
from .serializers import DiaryInputSerializer, diary_payload
from .stats import leaderboard, week_stats
from .views_util import parse_range


def _log(l):
    return {'id': str(l.id), 'user_id': str(l.user_id), 'clock_in': l.clock_in.isoformat(), 'clock_out': l.clock_out.isoformat() if l.clock_out else None}


class ClockView(APIView):
    """GET → the open clock-in (or null). Staff clock in/out to log working hours."""

    def get(self, request):
        l = TimeLog.objects.filter(user=request.user, clock_out__isnull=True).order_by('-clock_in').first()
        return Response({'open': _log(l) if l else None})


class ClockInView(APIView):
    def post(self, request):
        TimeLog.objects.filter(user=request.user, clock_out__isnull=True).update(clock_out=timezone.now())
        return Response(_log(TimeLog.objects.create(user=request.user)), status=status.HTTP_201_CREATED)


class ClockOutView(APIView):
    def post(self, request):
        l = TimeLog.objects.filter(user=request.user, clock_out__isnull=True).order_by('-clock_in').first()
        if l:
            l.clock_out = timezone.now()
            l.save(update_fields=['clock_out'])
        return Response({'open': None})


class MyWeekStatsView(APIView):
    def get(self, request):
        start, end = parse_range(request)
        return Response(week_stats(request.user, start, end, request.user.profile.weekly_hours_target))


class LeaderboardView(APIView):
    def get(self, request):
        try:
            days = max(1, min(int(request.query_params.get('days', 7)), 365))
        except ValueError:
            days = 7
        return Response(leaderboard(days, request))


class DiaryView(APIView):
    """GET → my entries (admin may pass ?user_id=). POST → create/update the entry for a date."""

    def get(self, request):
        target = request.user
        uid = request.query_params.get('user_id')
        if uid and str(uid) != str(request.user.id):
            if not request.user.profile.is_admin:
                return Response({'error': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)
            target = get_object_or_404(User, pk=uid)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 60)), 200))
        except ValueError:
            limit = 60
        return Response([diary_payload(d) for d in DiaryEntry.objects.filter(user=target)[:limit]])

    def post(self, request):
        ser = DiaryInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if d['entry_date'] > (timezone.now() + timedelta(days=1)).date():
            return Response({'error': "You can't write diary entries for future days."}, status=status.HTTP_400_BAD_REQUEST)
        entry, _ = DiaryEntry.objects.update_or_create(
            user=request.user, entry_date=d['entry_date'],
            defaults={'content': d['content'].strip(), 'mood': d.get('mood') or '', 'hours_worked': d.get('hours_worked') or 0})
        return Response(diary_payload(entry))


class DiaryCommentView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        entry = get_object_or_404(DiaryEntry, pk=pk)
        entry.admin_comment = str(request.data.get('comment', '')).strip()[:1000]
        entry.save(update_fields=['admin_comment'])
        return Response(diary_payload(entry))
