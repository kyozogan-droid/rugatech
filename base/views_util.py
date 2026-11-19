from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_range(request):
    """?start=&end= (ISO, from the phone's local Monday) — defaults to the current UTC week."""
    start, end = parse_datetime(request.query_params.get('start', '') or ''), parse_datetime(request.query_params.get('end', '') or '')
    if not (start and end):
        today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=7)
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)
    return start, end
