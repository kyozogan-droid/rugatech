"""
Deadline reminders: 24 hours before, 1 hour before, and an overdue alert — for tasks and calendar events.
Each (item, person, window) is sent exactly once (ReminderLog), so it is safe to run as often as you like.
Run it every ~10 minutes:  `python manage.py send_reminders`  (Render Cron Job)
or POST /api/internal/send-reminders/ with the X-Cron-Secret header (external pinger).
"""
import logging
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import email as mail
from .models import Event, ReminderLog, TaskAssignment
from .services import notify, notify_admins

logger = logging.getLogger(__name__)
HOUR = timedelta(hours=1)


def _claim(kind, ref_id, user, window):
    """Atomically reserve the slot; False means it was already sent (or another run just took it)."""
    try:
        with transaction.atomic():
            ReminderLog.objects.create(kind=kind, ref_id=str(ref_id), user=user, window=window)
        return True
    except IntegrityError:
        return False


def run(now=None):
    now = now or timezone.now()
    stats = {'task': 0, 'event': 0, 'overdue': 0}

    # ── Tasks ──
    rows = (TaskAssignment.objects.exclude(status='done')
            .filter(task__deadline__gte=now - timedelta(days=3), task__deadline__lte=now + 24 * HOUR, user__is_active=True)
            .select_related('task', 'user', 'user__profile'))
    for a in rows:
        t, user = a.task, a.user
        remaining = t.deadline - now
        if remaining <= timedelta(0):
            window = 'overdue'
        elif remaining <= HOUR:
            window = '1h'
        elif t.deadline - t.created_at > 24 * HOUR:
            window = '24h'
        else:
            continue
        if not _claim('task', t.id, user, window):
            continue

        when = t.deadline.strftime('%a %d %b, %H:%M UTC')
        if window == 'overdue':
            title, body = f'Overdue: {t.title}', f'This task passed its deadline ({when}). Please finish it or update your admin.'
        elif window == '1h':
            title, body = f'Due in 1 hour: {t.title}', f'Deadline: {when}.'
        else:
            title, body = f'Due in 24 hours: {t.title}', f'Deadline: {when}.'
        notify(user, title, body, 'overdue' if window == 'overdue' else 'reminder', t.id)
        if user.profile.email_reminders:
            mail.send_reminder(user, title, body, urgent=window != '24h')
        if window == 'overdue':
            stats['overdue'] += 1
            notify_admins(f'Overdue task — {user.get_full_name() or user.email}', f'"{t.title}" was due {when} and is not done.', 'overdue', t.id)
        else:
            stats['task'] += 1

    # ── Events (24h and 1h before start) ──
    events = Event.objects.filter(starts_at__gt=now, starts_at__lte=now + 24 * HOUR)
    everyone = None
    for e in events:
        remaining = e.starts_at - now
        window = '1h' if remaining <= HOUR else ('24h' if e.starts_at - e.created_at > 24 * HOUR else None)
        if not window:
            continue
        if e.is_company:
            if everyone is None:
                everyone = list(User.objects.filter(is_active=True, profile__profile_complete=True).select_related('profile'))
            recipients = everyone
        else:
            recipients = [e.created_by] if e.created_by and e.created_by.is_active else []
        when = e.starts_at.strftime('%a %d %b, %H:%M UTC')
        for user in recipients:
            if not _claim('event', e.id, user, window):
                continue
            title = f'{"Starting within 1 hour" if window == "1h" else "Tomorrow"}: {e.title}'
            notify(user, title, f'Starts {when}.', 'reminder', e.id)
            if user.profile.email_reminders:
                mail.send_reminder(user, title, f'Starts {when}.')
            stats['event'] += 1
    logger.info('send_reminders: %s', stats)
    return stats
