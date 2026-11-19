import secrets

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reminders


class SendRemindersView(APIView):
    """For hosts without cron jobs: ping this every 10 minutes (cron-job.org, UptimeRobot, GitHub Actions…)."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        expected = settings.CRON_SECRET
        given = request.headers.get('X-Cron-Secret', '')
        if not expected or not secrets.compare_digest(expected, given):
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({'ok': True, **reminders.run()})
