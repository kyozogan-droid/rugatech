import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


def avatar_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg').lower()[:5]
    return f'avatars/{instance.user_id}/{uuid.uuid4().hex}.{ext}'


def stored_file_path(instance, filename):
    safe = ''.join(c if c.isalnum() or c in '._-' else '_' for c in filename)[:120]
    return f'files/{instance.owner_id}/{uuid.uuid4().hex[:10]}-{safe}'


# ── Profile ───────────────────────────────────────────────────────────────────

class Profile(models.Model):
    ROLE_CHOICES = [('admin', 'Admin'), ('staff', 'Staff')]
    STAGE_CHOICES = [
        ('pending', 'Pending approval'),
        ('onboarding', 'Onboarding'),
        ('active', 'Active'),
        ('confirmed', 'Confirmed'),
    ]
    STAGES = [s for s, _ in STAGE_CHOICES]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='staff')
    stage = models.CharField(max_length=12, choices=STAGE_CHOICES, default='pending')
    email_verified = models.BooleanField(default=False)
    profile_complete = models.BooleanField(default=False)

    # Public to the team (shown on task cards, leaderboard)
    photo = models.ImageField(upload_to=avatar_path, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    department = models.CharField(max_length=80, blank=True)
    job_title = models.CharField(max_length=80, blank=True)
    staff_id = models.CharField(max_length=20, blank=True, null=True, unique=True)
    start_date = models.DateField(null=True, blank=True)

    # Private registration details (owner + admin only)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, blank=True)
    national_id = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    emergency_name = models.CharField(max_length=100, blank=True)
    emergency_phone = models.CharField(max_length=20, blank=True)
    emergency_relation = models.CharField(max_length=50, blank=True)

    weekly_hours_target = models.DecimalField(max_digits=5, decimal_places=1, default=40)
    push_token = models.CharField(max_length=200, blank=True)
    email_reminders = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['role', 'stage'])]

    def __str__(self):
        return f'{self.user.get_full_name() or self.user.email} ({self.role})'

    @property
    def is_admin(self):
        return self.role == 'admin' or self.user.is_superuser

    @property
    def approved(self):
        return self.stage != 'pending'


class ApprovalRecord(models.Model):
    member = models.ForeignKey(User, on_delete=models.CASCADE, related_name='approval_records')
    stage_from = models.CharField(max_length=12, blank=True)
    stage_to = models.CharField(max_length=12)
    note = models.TextField(blank=True)
    approved_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ── Email codes (verification + password reset) ───────────────────────────────

class EmailCode(models.Model):
    PURPOSES = [('verify', 'Verify email'), ('reset', 'Reset password')]
    MAX_ATTEMPTS = 5
    TTL_MINUTES = 15

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_codes')
    purpose = models.CharField(max_length=10, choices=PURPOSES)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'purpose', '-created_at'])]

    @staticmethod
    def _hash(user_id, purpose, code):
        return hashlib.sha256(f'{settings.SECRET_KEY}:{user_id}:{purpose}:{code}'.encode()).hexdigest()

    @classmethod
    def issue(cls, user, purpose):
        """Create a fresh 6-digit code (invalidating older ones). Returns the plain code."""
        cls.objects.filter(user=user, purpose=purpose, used=False).update(used=True)
        code = f'{secrets.randbelow(1_000_000):06d}'
        cls.objects.create(user=user, purpose=purpose, code_hash=cls._hash(user.id, purpose, code),
                           expires_at=timezone.now() + timedelta(minutes=cls.TTL_MINUTES))
        return code

    @classmethod
    def verify(cls, user, purpose, code):
        """True if `code` is the live code for this user/purpose. Burns it on success; counts failures."""
        rec = cls.objects.filter(user=user, purpose=purpose, used=False).order_by('-created_at').first()
        if not rec or rec.expires_at < timezone.now() or rec.attempts >= cls.MAX_ATTEMPTS:
            return False
        if not secrets.compare_digest(rec.code_hash, cls._hash(user.id, purpose, str(code).strip())):
            rec.attempts += 1
            rec.save(update_fields=['attempts'])
            return False
        rec.used = True
        rec.save(update_fields=['used'])
        return True


# ── Tasks ─────────────────────────────────────────────────────────────────────

class Task(models.Model):
    PRIORITIES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='medium')
    deadline = models.DateTimeField()
    estimated_minutes = models.PositiveIntegerField(default=60)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='created_tasks')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['deadline']

    def __str__(self):
        return self.title


class TaskAssignment(models.Model):
    """One row per (task, person) — each assignee has their own status and clock."""
    STATUSES = [('todo', 'To do'), ('in_progress', 'In progress'), ('done', 'Done')]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='assignments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignments')
    status = models.CharField(max_length=12, choices=STATUSES, default='todo')
    started_at = models.DateTimeField(null=True, blank=True)
    timer_started_at = models.DateTimeField(null=True, blank=True)   # set while the clock runs
    actual_seconds = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    is_late = models.BooleanField(default=False)

    class Meta:
        unique_together = [('task', 'user')]
        indexes = [models.Index(fields=['user', 'status'])]


# ── Diary / hours / calendar ──────────────────────────────────────────────────

class DiaryEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='diary_entries')
    entry_date = models.DateField()
    content = models.TextField()
    mood = models.CharField(max_length=10, blank=True)
    hours_worked = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    admin_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'entry_date')]
        ordering = ['-entry_date']


class TimeLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='time_logs')
    clock_in = models.DateTimeField(default=timezone.now)
    clock_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'clock_in'])]


class Event(models.Model):
    KINDS = [('event', 'Event'), ('meeting', 'Meeting'), ('deadline', 'Deadline')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=10, choices=KINDS, default='event')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    is_company = models.BooleanField(default=False)    # admin-created, visible to everyone
    created_by = models.ForeignKey(User, null=True, on_delete=models.CASCADE, related_name='events')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['starts_at']


# ── Documents & files ─────────────────────────────────────────────────────────

class Document(models.Model):
    KINDS = [('meeting', 'Meeting minutes'), ('report', 'Report'), ('event', 'Event write-up')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=KINDS, default='report')
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    shared = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class StoredFile(models.Model):
    CATEGORIES = [('document', 'Document'), ('photo', 'Photo'), ('other', 'Other')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stored_files')
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to=stored_file_path)
    mime = models.CharField(max_length=100, blank=True)
    size = models.BigIntegerField(default=0)
    category = models.CharField(max_length=10, choices=CATEGORIES, default='document')
    shared = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ── Notifications, reminders, audit, AI ───────────────────────────────────────

class Notification(models.Model):
    KINDS = [('info', 'Info'), ('task', 'Task'), ('reminder', 'Reminder'), ('overdue', 'Overdue'), ('approval', 'Approval')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=10, choices=KINDS, default='info')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    ref_id = models.CharField(max_length=40, blank=True)     # task / event id for deep links
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at', 'is_read'])]


class ReminderLog(models.Model):
    """De-duplicates reminders: each (kind, ref, user, window) is sent exactly once."""
    kind = models.CharField(max_length=10)       # task | event
    ref_id = models.CharField(max_length=40)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    window = models.CharField(max_length=10)     # 24h | 1h | overdue
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('kind', 'ref_id', 'user', 'window')]


class AuditLog(models.Model):
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs')
    action = models.CharField(max_length=40)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class AIMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ai_messages')
    role = models.CharField(max_length=10)       # user | assistant
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
