"""Weekly progress + leaderboard calculations."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from .models import DiaryEntry, TaskAssignment, TimeLog


def hours_between(user, start, end, include_diary=True):
    """Hours worked = clocked time (+ diary-reported hours on days with no clock-in)."""
    now = timezone.now()
    logs = TimeLog.objects.filter(user=user, clock_in__gte=start, clock_in__lt=end)
    hours, clocked_days = 0.0, set()
    for l in logs:
        hours += max(0.0, ((l.clock_out or now) - l.clock_in).total_seconds()) / 3600
        clocked_days.add(l.clock_in.date())
    if include_diary:
        for d in DiaryEntry.objects.filter(user=user, entry_date__gte=start.date(), entry_date__lt=end.date()):
            if d.entry_date not in clocked_days:
                hours += float(d.hours_worked)
    return round(hours, 1)


def week_stats(user, start, end, target):
    now = timezone.now()
    rows = list(TaskAssignment.objects.filter(user=user, task__deadline__gte=start, task__deadline__lt=end).select_related('task'))
    total = len(rows)
    done = sum(1 for r in rows if r.status == 'done')
    overdue = sum(1 for r in rows if r.status != 'done' and r.task.deadline < now)
    late = sum(1 for r in rows if r.status == 'done' and r.is_late)
    hours = hours_between(user, start, end)
    diary_days = DiaryEntry.objects.filter(user=user, entry_date__gte=start.date(), entry_date__lt=end.date()).count()
    target = float(target) or 40.0
    return {
        'tasksTotal': total, 'tasksDone': done, 'overdue': overdue, 'lateDone': late, 'hours': hours,
        'hoursTarget': target, 'diaryDays': diary_days,
        'taskFraction': (done / total) if total else 0, 'hoursFraction': min(1.0, hours / target),
    }


def leaderboard(days, request):
    """Score = on-time tasks×10 + late tasks×4 + diary days×3 + min(hours, 60)."""
    from .serializers import full_name, photo_url
    since = timezone.now() - timedelta(days=days)
    end = timezone.now() + timedelta(days=1)
    out = []
    for user in User.objects.filter(is_active=True, profile__role='staff').select_related('profile'):
        done = TaskAssignment.objects.filter(user=user, status='done', completed_at__gte=since)
        on_time = done.filter(is_late=False).count()
        late = done.filter(is_late=True).count()
        diary_days = DiaryEntry.objects.filter(user=user, entry_date__gte=since.date()).count()
        hours = hours_between(user, since, end, include_diary=False)
        out.append({
            'user_id': str(user.id), 'full_name': full_name(user), 'photo_url': photo_url(user.profile, request),
            'job_title': user.profile.job_title or None, 'tasks_done': on_time + late, 'on_time': on_time, 'late': late,
            'hours': hours, 'diary_days': diary_days,
            'score': round(on_time * 10 + late * 4 + diary_days * 3 + min(hours, 60), 1),
        })
    out.sort(key=lambda r: (-r['score'], -r['tasks_done']))
    return out
