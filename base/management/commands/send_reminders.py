# Schedule every 10 minutes (Render Cron Job, see render.yaml):
#   python manage.py send_reminders
from django.core.management.base import BaseCommand

from base import reminders


class Command(BaseCommand):
    help = 'Send 24h / 1h / overdue reminders for tasks and events (idempotent).'

    def handle(self, *args, **options):
        s = reminders.run()
        self.stdout.write(self.style.SUCCESS(f"Reminders sent — tasks: {s['task']}, events: {s['event']}, overdue alerts: {s['overdue']}"))
