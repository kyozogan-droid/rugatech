from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Every account gets a Profile. Emails in ADMIN_EMAILS (and superusers) become admins."""
    if not created:
        return
    is_admin = instance.is_superuser or (instance.email or '').lower() in settings.ADMIN_EMAILS
    profile, _ = Profile.objects.get_or_create(user=instance, defaults={
        'role': 'admin' if is_admin else 'staff',
        'stage': 'confirmed' if is_admin else 'pending',
    })
    if not profile.staff_id:
        profile.staff_id = f'RGT-{instance.id:04d}'
        profile.save(update_fields=['staff_id'])
