# python manage.py create_admin you@rugatech.com            (prompts for a password)
# python manage.py create_admin you@rugatech.com --password 'S0me-Strong-Pass'
import getpass

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from base.models import Profile


class Command(BaseCommand):
    help = 'Create (or promote) an admin account for the Rugatech staff app.'

    def add_arguments(self, parser):
        parser.add_argument('email')
        parser.add_argument('--name', default='')
        parser.add_argument('--password', default='')

    def handle(self, *args, **o):
        email = o['email'].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            password = o['password'] or getpass.getpass('Password: ')
            if len(password) < 8:
                raise CommandError('Password must be at least 8 characters.')
            parts = (o['name'] or email.split('@')[0]).split()
            user = User.objects.create_user(username=email, email=email, password=password, first_name=parts[0], last_name=' '.join(parts[1:]))
        Profile.objects.filter(user=user).update(role='admin', stage='confirmed', email_verified=True)
        self.stdout.write(self.style.SUCCESS(f'{email} is now an admin.'))
