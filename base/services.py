"""Small shared helpers used by several views."""
from django.contrib.auth.models import User

from .models import AuditLog, Notification
from .push import send_push


def client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None


def audit(request, action, user=None, **details):
    AuditLog.objects.create(
        user=user or (request.user if getattr(request, 'user', None) and request.user.is_authenticated else None),
        action=action, ip_address=client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:300], details=details)


def notify(user, title, body='', kind='info', ref_id='', push=True):
    """Create an in-app notification and (optionally) push it to the user's phone."""
    n = Notification.objects.create(user=user, kind=kind, title=title, description=body, ref_id=str(ref_id or ''))
    if push:
        token = getattr(getattr(user, 'profile', None), 'push_token', '')
        send_push([token], title, body, {'type': 'task' if kind in ('task', 'reminder', 'overdue') and ref_id else 'info', 'id': str(ref_id or '')})
    return n


def notify_admins(title, body='', kind='info', ref_id=''):
    for admin in User.objects.filter(profile__role='admin', is_active=True).select_related('profile'):
        notify(admin, title, body, kind, ref_id)
