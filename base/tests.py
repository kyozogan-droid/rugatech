import io
import re
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from . import reminders
from .models import Event, Notification, ReminderLog, Task, TaskAssignment

MEDIA = tempfile.mkdtemp()


def png_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (214, 181, 131)).save(buf, 'PNG')
    return buf.getvalue()


def make_user(email, password='Str0ng-Pass-123', first='Test', last='User', admin=False, complete=True):
    u = User.objects.create_user(username=email, email=email, password=password, first_name=first, last_name=last)
    p = u.profile
    p.email_verified = True
    if admin:
        p.role, p.stage = 'admin', 'confirmed'
    p.profile_complete = complete
    p.save()
    return u


def client_for(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.get_or_create(user=user)[0].key}')
    return c


@override_settings(MEDIA_ROOT=MEDIA)
class BaseCase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_user('boss@rugatech.com', first='Boss', last='Man', admin=True)
        self.amina = make_user('amina@rugatech.com', first='Amina', last='Otieno')
        self.brian = make_user('brian@rugatech.com', first='Brian', last='Kamau')
        self.a, self.am, self.br = client_for(self.admin), client_for(self.amina), client_for(self.brian)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def mk_task(self, users=None, hours=48, title='Install CCTV', created_ago=timedelta(days=3)):
        t = Task.objects.create(title=title, deadline=timezone.now() + timedelta(hours=hours), estimated_minutes=60, created_by=self.admin)
        Task.objects.filter(pk=t.pk).update(created_at=timezone.now() - created_ago)
        for u in users or [self.amina]:
            TaskAssignment.objects.create(task=t, user=u)
        return Task.objects.get(pk=t.pk)


class AuthTests(BaseCase):
    def test_register_verify_login_flow(self):
        c = APIClient()
        r = c.post('/api/auth/register/', {'full_name': 'Neema Wanjiru', 'email': 'Neema@Rugatech.com', 'password': 'Gr8-Password-77'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()['needs_verification'])
        self.assertTrue(re.search(r'\b(\d{6})\b', mail.outbox[-1].body))

        r = c.post('/api/auth/login/', {'email': 'neema@rugatech.com', 'password': 'Gr8-Password-77'}, format='json')
        self.assertEqual(r.status_code, 403)                 # cannot sign in before verifying
        self.assertTrue(r.json()['needs_verification'])

        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)   # login re-issued a fresh code
        wrong = '000000' if code != '000000' else '111111'
        self.assertEqual(c.post('/api/auth/verify-email/', {'email': 'neema@rugatech.com', 'code': wrong}, format='json').status_code, 400)

        r = c.post('/api/auth/verify-email/', {'email': 'neema@rugatech.com', 'code': code}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['token'])
        self.assertEqual((body['user']['role'], body['user']['stage']), ('staff', 'pending'))
        self.assertFalse(body['user']['profile_complete'])
        self.assertEqual(body['user']['full_name'], 'Neema Wanjiru')
        self.assertTrue(Notification.objects.filter(user=self.admin, title='New staff member').exists())
        self.assertEqual(c.post('/api/auth/login/', {'email': 'neema@rugatech.com', 'password': 'Gr8-Password-77'}, format='json').status_code, 200)

    def test_code_locks_after_five_wrong_attempts(self):
        c = APIClient()
        c.post('/api/auth/register/', {'full_name': 'Neema W', 'email': 'n@rugatech.com', 'password': 'Gr8-Password-77'}, format='json')
        real = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        wrong = '123456' if real != '123456' else '654321'
        for _ in range(5):
            cache.clear()
            c.post('/api/auth/verify-email/', {'email': 'n@rugatech.com', 'code': wrong}, format='json')
        cache.clear()
        self.assertEqual(c.post('/api/auth/verify-email/', {'email': 'n@rugatech.com', 'code': real}, format='json').status_code, 400)

    def test_weak_and_duplicate_registration_rejected(self):
        c = APIClient()
        self.assertEqual(c.post('/api/auth/register/', {'full_name': 'Ab Cd', 'email': 'x@rugatech.com', 'password': '12345678'}, format='json').status_code, 400)
        self.assertEqual(c.post('/api/auth/register/', {'full_name': 'Ab Cd', 'email': 'AMINA@rugatech.com', 'password': 'Gr8-Password-77'}, format='json').status_code, 400)

    @override_settings(ADMIN_EMAILS=['owner@rugatech.com'], REQUIRE_EMAIL_VERIFICATION=False)
    def test_admin_email_allowlist_and_no_verification_mode(self):
        r = APIClient().post('/api/auth/register/', {'full_name': 'The Owner', 'email': 'owner@rugatech.com', 'password': 'Gr8-Password-77'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()['user']['role'], r.json()['user']['stage']), ('admin', 'confirmed'))

    def test_stale_token_does_not_break_public_endpoints(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION='Token deadbeef')
        self.assertEqual(c.post('/api/auth/login/', {'email': 'amina@rugatech.com', 'password': 'Str0ng-Pass-123'}, format='json').status_code, 200)

    def test_wrong_password_and_lockout(self):
        c = APIClient()
        for _ in range(8):
            self.assertEqual(c.post('/api/auth/login/', {'email': 'amina@rugatech.com', 'password': 'nope-nope-1'}, format='json').status_code, 400)
        self.assertEqual(c.post('/api/auth/login/', {'email': 'amina@rugatech.com', 'password': 'Str0ng-Pass-123'}, format='json').status_code, 429)

    def test_password_reset_flow_signs_out_everywhere(self):
        c = APIClient()
        c.post('/api/auth/forgot-password/', {'email': 'amina@rugatech.com'}, format='json')
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        n = len(mail.outbox)
        self.assertEqual(c.post('/api/auth/forgot-password/', {'email': 'ghost@x.com'}, format='json').status_code, 200)
        self.assertEqual(len(mail.outbox), n)                # unknown email: same answer, no email
        r = c.post('/api/auth/reset-password/', {'email': 'amina@rugatech.com', 'code': code, 'password': 'Brand-New-Pass-9'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.am.get('/api/auth/user/').status_code, 401)     # old token revoked
        self.assertEqual(c.post('/api/auth/login/', {'email': 'amina@rugatech.com', 'password': 'Brand-New-Pass-9'}, format='json').status_code, 200)

    def test_change_password(self):
        self.assertEqual(self.am.post('/api/auth/user/change-password/', {'current_password': 'wrong', 'new_password': 'Another-Pass-55'}, format='json').status_code, 400)
        r = self.am.post('/api/auth/user/change-password/', {'current_password': 'Str0ng-Pass-123', 'new_password': 'Another-Pass-55'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['token'])

    def test_unauthenticated_is_rejected_everywhere_private(self):
        c = APIClient()
        for url in ['/api/tasks/', '/api/members/', '/api/auth/user/', '/api/files/', '/api/leaderboard/', '/api/notifications/']:
            self.assertEqual(c.get(url).status_code, 401, url)

    def test_logout_revokes_token(self):
        self.assertEqual(self.am.post('/api/auth/logout/').status_code, 200)
        self.assertEqual(self.am.get('/api/auth/user/').status_code, 401)

    def test_delete_account_blocks_last_admin(self):
        self.assertEqual(self.a.post('/api/auth/user/delete-account/', {'password': 'Str0ng-Pass-123'}, format='json').status_code, 400)
        self.assertEqual(self.am.post('/api/auth/user/delete-account/', {'password': 'Str0ng-Pass-123'}, format='json').status_code, 200)
        self.assertFalse(User.objects.filter(email='amina@rugatech.com').exists())


class GoogleTests(BaseCase):
    def _fake(self, aud='web-client', email_verified='true'):
        def get(url, params=None, headers=None, timeout=None):
            m = mock.Mock(status_code=200)
            if 'userinfo' in url:
                m.json.return_value = {'email': 'newbie@gmail.com', 'given_name': 'New', 'family_name': 'Bie', 'email_verified': True}
            else:
                m.json.return_value = {'aud': aud, 'email': 'newbie@gmail.com', 'email_verified': email_verified}
            return m
        return get

    def test_not_configured(self):
        self.assertEqual(APIClient().post('/api/auth/google/callback/', {'access_token': 'x'}, format='json').status_code, 503)

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=['web-client', 'android-client'])
    def test_token_for_another_app_is_rejected(self):
        with mock.patch('base.auth_views.requests.get', self._fake(aud='someone-elses-app')):
            r = APIClient().post('/api/auth/google/callback/', {'access_token': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(email='newbie@gmail.com').exists())

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=['web-client', 'android-client'])
    def test_valid_google_login_creates_verified_staff(self):
        with mock.patch('base.auth_views.requests.get', self._fake(aud='android-client')):
            r = APIClient().post('/api/auth/google/callback/', {'access_token': 'x'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertTrue(b['is_new_user'])
        self.assertEqual(b['user']['role'], 'staff')
        self.assertTrue(b['user']['email_verified'])
        self.assertEqual(b['user']['full_name'], 'New Bie')

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=['web-client'])
    def test_unverified_google_email_rejected(self):
        with mock.patch('base.auth_views.requests.get', self._fake(aud='web-client', email_verified='false')):
            r = APIClient().post('/api/auth/google/callback/', {'id_token': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)


class ProfileTests(BaseCase):
    FORM = {'full_name': 'Amina Otieno', 'phone': '+254700111222', 'job_title': 'Technician', 'department': 'Ops',
            'date_of_birth': '1996-04-02', 'gender': 'Female', 'national_id': '12345678', 'address': 'Nakuru town',
            'emergency_name': 'Joy Otieno', 'emergency_phone': '+254711222333', 'emergency_relation': 'Sister'}

    def test_registration_requires_everything_including_photo(self):
        c = client_for(make_user('new@rugatech.com', complete=False))
        r = c.put('/api/auth/profile/', self.FORM, format='multipart')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['profile_complete'])
        self.assertIn('a profile photo', r.json()['missing_fields'])

        photo = SimpleUploadedFile('me.png', png_bytes(), content_type='image/png')
        r = c.put('/api/auth/profile/', {**self.FORM, 'photo': photo}, format='multipart')
        self.assertTrue(r.json()['profile_complete'], r.json().get('missing_fields'))
        self.assertTrue(r.json()['photo_url'])
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Registration completed').exists())

        img = APIClient().get(r.json()['photo_url'].replace('http://testserver', ''))      # public avatar
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img['Content-Type'], 'image/png')

    def test_private_details_hidden_from_teammates_but_visible_to_admin(self):
        self.amina.profile.national_id = 'SECRET-1'
        self.amina.profile.save()
        self.assertEqual(self.br.get(f'/api/members/{self.amina.id}/').status_code, 403)
        self.assertEqual(self.a.get(f'/api/members/{self.amina.id}/').json()['national_id'], 'SECRET-1')
        self.assertNotIn('national_id', self.a.get('/api/members/').json()[0])

    def test_staff_cannot_self_promote(self):
        self.am.put('/api/auth/profile/', {'role': 'admin', 'stage': 'confirmed'}, format='multipart')
        self.amina.profile.refresh_from_db()
        self.assertEqual((self.amina.profile.role, self.amina.profile.stage), ('staff', 'pending'))


class ApprovalTests(BaseCase):
    def test_admin_approves_stage_by_stage_with_history_and_notification(self):
        r = self.a.post(f'/api/members/{self.amina.id}/approve/', {'stage': 'onboarding', 'note': 'Welcome aboard'}, format='json')
        self.assertEqual((r.status_code, r.json()['stage']), (200, 'onboarding'))
        self.a.post(f'/api/members/{self.amina.id}/approve/', {'stage': 'active'}, format='json')
        hist = self.a.get(f'/api/members/{self.amina.id}/approvals/').json()
        self.assertEqual([h['stage_to'] for h in hist], ['active', 'onboarding'])
        self.assertEqual(Notification.objects.filter(user=self.amina, kind='approval').count(), 2)
        self.assertEqual(len(self.am.get('/api/auth/approvals/').json()), 2)

    def test_staff_cannot_approve(self):
        self.assertEqual(self.am.post(f'/api/members/{self.brian.id}/approve/', {'stage': 'confirmed'}, format='json').status_code, 403)
        self.assertEqual(self.a.post(f'/api/members/{self.brian.id}/approve/', {'stage': 'bogus'}, format='json').status_code, 400)


class TaskTests(BaseCase):
    def payload(self, ids, **kw):
        return {'title': 'Install CCTV at client site', 'description': 'Bring ladder', 'priority': 'high',
                'deadline': (timezone.now() + timedelta(days=2)).isoformat(), 'estimated_minutes': 120, 'assignee_ids': ids, **kw}

    def test_only_admin_creates_and_multi_assign_notifies_everyone(self):
        self.assertEqual(self.am.post('/api/tasks/', self.payload([self.amina.id]), format='json').status_code, 403)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.a.post('/api/tasks/', self.payload([self.amina.id, self.brian.id]), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.json()['task_assignees']), 2)
        self.assertEqual(Notification.objects.filter(kind='task', title='New task assigned').count(), 2)
        self.assertEqual({m.to[0] for m in mail.outbox}, {'amina@rugatech.com', 'brian@rugatech.com'})
        self.assertEqual(self.a.post('/api/tasks/', self.payload([99999]), format='json').status_code, 400)

    def test_staff_only_see_their_tasks(self):
        mine = self.mk_task([self.amina], title='Mine')
        self.mk_task([self.brian], title='Not mine')
        self.assertEqual([t['title'] for t in self.am.get('/api/tasks/').json()], ['Mine'])
        self.assertEqual(self.am.get(f'/api/tasks/{mine.id}/').status_code, 200)
        self.assertEqual(self.br.get(f'/api/tasks/{mine.id}/').status_code, 404)
        self.assertEqual(len(self.a.get('/api/tasks/').json()), 2)
        self.assertEqual(self.am.delete(f'/api/tasks/{mine.id}/').status_code, 403)

    def test_server_side_timer_and_on_time_completion(self):
        t = self.mk_task([self.amina, self.brian])
        t0 = timezone.now()
        url = f'/api/tasks/{t.id}/status/'
        with mock.patch('base.task_views.timezone.now', return_value=t0):
            self.assertEqual(self.am.post(url, {'status': 'in_progress'}, format='json').json()['status'], 'in_progress')
        with mock.patch('base.task_views.timezone.now', return_value=t0 + timedelta(minutes=30)):
            self.am.post(url, {'status': 'todo'}, format='json')            # pause
        with mock.patch('base.task_views.timezone.now', return_value=t0 + timedelta(minutes=40)):
            self.am.post(url, {'status': 'in_progress'}, format='json')
        with mock.patch('base.task_views.timezone.now', return_value=t0 + timedelta(minutes=55)):
            d = self.am.post(url, {'status': 'done'}, format='json').json()
        self.assertEqual(d['actual_seconds'], 45 * 60)
        self.assertFalse(d['is_late'])
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Task completed').exists())
        other = TaskAssignment.objects.get(task=t, user=self.brian)         # Brian's clock is independent
        self.assertEqual((other.status, other.actual_seconds), ('todo', 0))

    def test_late_completion_is_flagged(self):
        t = self.mk_task([self.amina], hours=-2)
        self.assertTrue(self.am.post(f'/api/tasks/{t.id}/status/', {'status': 'done'}, format='json').json()['is_late'])
        self.assertTrue(Notification.objects.filter(user=self.admin, title='Task finished late').exists())

    def test_cannot_change_someone_elses_assignment(self):
        t = self.mk_task([self.amina])
        self.assertEqual(self.br.post(f'/api/tasks/{t.id}/status/', {'status': 'done'}, format='json').status_code, 403)
        self.assertEqual(self.a.post(f'/api/tasks/{t.id}/status/', {'status': 'done'}, format='json').status_code, 403)

    def test_edit_task_reassigns(self):
        t = self.mk_task([self.amina])
        r = self.a.patch(f'/api/tasks/{t.id}/', self.payload([self.brian.id], title='Renamed task'), format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([a['user_id'] for a in r.json()['task_assignees']], [str(self.brian.id)])
        self.assertEqual(self.am.get('/api/tasks/').json(), [])


class ReminderTests(BaseCase):
    def test_24h_then_1h_then_overdue_each_sent_once(self):
        t = self.mk_task([self.amina], hours=23)
        now = timezone.now()
        mail.outbox.clear()
        self.assertEqual(reminders.run(now)['task'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('24 hours', mail.outbox[0].subject)
        self.assertEqual(reminders.run(now + timedelta(minutes=10))['task'], 0)          # no duplicate
        self.assertEqual(len(mail.outbox), 1)

        self.assertEqual(reminders.run(t.deadline - timedelta(minutes=30))['task'], 1)
        self.assertIn('1 hour', mail.outbox[-1].subject)

        self.assertEqual(reminders.run(t.deadline + timedelta(minutes=5))['overdue'], 1)
        self.assertTrue(Notification.objects.filter(user=self.amina, kind='overdue').exists())
        self.assertTrue(Notification.objects.filter(user=self.admin, kind='overdue').exists())     # admin alerted
        self.assertEqual(ReminderLog.objects.filter(user=self.amina).count(), 3)

    def test_done_tasks_and_far_deadlines_are_ignored(self):
        done = self.mk_task([self.amina], hours=5, title='done one')
        TaskAssignment.objects.filter(task=done).update(status='done')
        self.mk_task([self.amina], hours=72, title='far away')
        self.assertEqual(reminders.run(), {'task': 0, 'event': 0, 'overdue': 0})

    def test_respects_email_opt_out_but_still_notifies_in_app(self):
        self.amina.profile.email_reminders = False
        self.amina.profile.save()
        self.mk_task([self.amina], hours=0.5)
        mail.outbox.clear()
        reminders.run()
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(Notification.objects.filter(user=self.amina, kind='reminder').exists())

    def test_task_created_inside_24h_skips_the_24h_reminder(self):
        self.mk_task([self.amina], hours=10, created_ago=timedelta(hours=1))
        self.assertEqual(reminders.run()['task'], 0)

    def test_company_event_reminds_everyone_complete(self):
        e = Event.objects.create(title='Team meeting', kind='meeting', starts_at=timezone.now() + timedelta(hours=20), is_company=True, created_by=self.admin)
        Event.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(days=2))
        self.assertEqual(reminders.run()['event'], 3)          # admin + 2 staff

    def test_cron_endpoint_requires_secret(self):
        c = APIClient()
        with override_settings(CRON_SECRET='s3cret'):
            self.assertEqual(c.post('/api/internal/send-reminders/').status_code, 401)
            self.assertEqual(c.post('/api/internal/send-reminders/', HTTP_X_CRON_SECRET='wrong').status_code, 401)
            self.assertEqual(c.post('/api/internal/send-reminders/', HTTP_X_CRON_SECRET='s3cret').status_code, 200)
        with override_settings(CRON_SECRET=''):
            self.assertEqual(c.post('/api/internal/send-reminders/', HTTP_X_CRON_SECRET='').status_code, 401)


class WorkTests(BaseCase):
    def test_clock_in_out_and_week_stats(self):
        self.assertIsNone(self.am.get('/api/work/clock/').json()['open'])
        self.assertEqual(self.am.post('/api/work/clock-in/').status_code, 201)
        self.assertIsNotNone(self.am.get('/api/work/clock/').json()['open'])
        self.am.post('/api/work/clock-out/')
        self.assertIsNone(self.am.get('/api/work/clock/').json()['open'])

        t = self.mk_task([self.amina], hours=1)
        self.am.post(f'/api/tasks/{t.id}/status/', {'status': 'done'}, format='json')
        s = self.am.get('/api/work/week-stats/', {'start': (timezone.now() - timedelta(days=1)).isoformat(),
                                                    'end': (timezone.now() + timedelta(days=6)).isoformat()}).json()
        self.assertEqual((s['tasksTotal'], s['tasksDone'], s['taskFraction']), (1, 1, 1))

    def test_diary_upsert_comment_and_future_guard(self):
        today = timezone.now().date().isoformat()
        r = self.am.post('/api/diary/', {'entry_date': today, 'content': 'Installed cameras', 'mood': 'good', 'hours_worked': 7.5}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.am.post('/api/diary/', {'entry_date': today, 'content': 'Installed cameras and tested', 'hours_worked': 8}, format='json')
        entries = self.am.get('/api/diary/').json()
        self.assertEqual(len(entries), 1)                          # upsert, not duplicate
        self.assertEqual(entries[0]['hours_worked'], 8.0)
        far = (timezone.now() + timedelta(days=10)).date().isoformat()
        self.assertEqual(self.am.post('/api/diary/', {'entry_date': far, 'content': 'future!'}, format='json').status_code, 400)

        self.assertEqual(self.br.get('/api/diary/', {'user_id': self.amina.id}).status_code, 403)
        self.assertEqual(len(self.a.get('/api/diary/', {'user_id': self.amina.id}).json()), 1)
        r = self.a.post(f'/api/diary/{entries[0]["id"]}/comment/', {'comment': 'Great work'}, format='json')
        self.assertEqual(r.json()['admin_comment'], 'Great work')
        self.assertEqual(self.am.post(f'/api/diary/{entries[0]["id"]}/comment/', {'comment': 'self-praise'}, format='json').status_code, 403)

    def test_leaderboard_ranks_by_score(self):
        for i in range(2):
            t = self.mk_task([self.amina], hours=5, title=f'T{i}')
            self.am.post(f'/api/tasks/{t.id}/status/', {'status': 'done'}, format='json')
        late = self.mk_task([self.brian], hours=-1, title='late')
        self.br.post(f'/api/tasks/{late.id}/status/', {'status': 'done'}, format='json')
        rows = self.am.get('/api/leaderboard/').json()
        self.assertEqual([r['full_name'] for r in rows], ['Amina Otieno', 'Brian Kamau'])
        self.assertEqual((rows[0]['on_time'], rows[0]['score']), (2, 20.0))
        self.assertEqual((rows[1]['late'], rows[1]['score']), (1, 4.0))
        self.assertNotIn(self.admin.id, [int(r['user_id']) for r in rows])            # admins aren't ranked


class ContentTests(BaseCase):
    def test_event_visibility_and_company_rights(self):
        when = (timezone.now() + timedelta(days=1)).isoformat()
        self.am.post('/api/events/', {'title': 'Dentist', 'starts_at': when, 'is_company': True}, format='json')   # staff can't go company-wide
        self.a.post('/api/events/', {'title': 'All-hands', 'starts_at': when, 'is_company': True}, format='json')
        self.assertEqual([e['title'] for e in self.br.get('/api/events/').json()], ['All-hands'])
        mine = self.am.get('/api/events/').json()
        self.assertEqual({e['title'] for e in mine}, {'Dentist', 'All-hands'})
        dentist = next(e for e in mine if e['title'] == 'Dentist')
        self.assertFalse(dentist['is_company'])
        self.assertEqual(self.br.delete(f'/api/events/{dentist["id"]}/').status_code, 403)
        self.assertEqual(self.am.delete(f'/api/events/{dentist["id"]}/').status_code, 204)

    def test_documents_private_until_shared_admin_sees_all(self):
        did = self.am.post('/api/documents/', {'kind': 'meeting', 'title': 'Minutes', 'content': 'Notes', 'shared': False}, format='json').json()['id']
        self.assertEqual(self.br.get('/api/documents/').json(), [])
        self.assertEqual(len(self.a.get('/api/documents/').json()), 1)
        self.am.patch(f'/api/documents/{did}/', {'kind': 'meeting', 'title': 'Minutes', 'content': 'Notes v2', 'shared': True}, format='json')
        self.assertEqual(len(self.br.get('/api/documents/').json()), 1)
        self.assertEqual(self.br.delete(f'/api/documents/{did}/').status_code, 404)        # only the owner deletes

    def test_files_upload_signed_link_sharing_and_delete(self):
        up = SimpleUploadedFile('contract.pdf', b'%PDF-1.4 hello', content_type='application/pdf')
        r = self.am.post('/api/files/', {'file': up, 'shared': 'false'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        fid = r.json()['id']
        self.assertEqual(r.json()['category'], 'document')
        self.assertEqual(self.br.get('/api/files/').json(), [])
        self.assertEqual(len(self.a.get('/api/files/').json()), 1)                           # admin sees all
        self.assertEqual(self.br.get(f'/api/files/{fid}/link/').status_code, 404)

        link = self.am.get(f'/api/files/{fid}/link/').json()['url']
        dl = APIClient().get(link.replace('http://testserver', ''))                          # no auth header needed
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(b''.join(dl.streaming_content), b'%PDF-1.4 hello')
        self.assertEqual(APIClient().get('/api/files/download/tampered-token/').status_code, 404)

        self.am.patch(f'/api/files/{fid}/', {'shared': True}, format='json')
        self.assertEqual(len(self.br.get('/api/files/').json()), 1)
        self.assertEqual(self.br.delete(f'/api/files/{fid}/').status_code, 404)
        self.assertEqual(self.am.delete(f'/api/files/{fid}/').status_code, 204)

    def test_oversize_upload_rejected(self):
        with override_settings(MAX_UPLOAD_BYTES=10):
            r = self.am.post('/api/files/', {'file': SimpleUploadedFile('a.txt', b'x' * 50)}, format='multipart')
        self.assertEqual(r.status_code, 400)

    def test_notifications_read_and_count(self):
        Notification.objects.create(user=self.amina, title='A')
        Notification.objects.create(user=self.amina, title='B')
        self.assertEqual(self.am.get('/api/notifications/unread-count/').json()['count'], 2)
        nid = self.am.get('/api/notifications/').json()[0]['id']
        self.am.post(f'/api/notifications/{nid}/read/')
        self.assertEqual(self.am.get('/api/notifications/unread-count/').json()['count'], 1)
        self.am.post('/api/notifications/read-all/')
        self.assertEqual(self.am.get('/api/notifications/unread-count/').json()['count'], 0)
        self.assertEqual(self.br.get('/api/notifications/').json(), [])                      # nobody else's


class AITests(BaseCase):
    def test_unconfigured_ai_returns_clear_error(self):
        r = self.am.post('/api/ai/follow-up/')
        self.assertEqual(r.status_code, 503)
        self.assertIn('not configured', r.json()['error'])

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_chat_uses_only_the_callers_tasks_and_saves_history(self):
        self.mk_task([self.amina], title='Amina secret job')
        self.mk_task([self.brian], title='Brian private job')
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            captured['sent'] = json['system'] + ' ' + ' '.join(m['content'] for m in json['messages'])
            m = mock.Mock(status_code=200)
            m.json.return_value = {'content': [{'type': 'text', 'text': 'Start with the CCTV job.'}]}
            return m

        with mock.patch('base.ai.requests.post', fake_post):
            r = self.am.post('/api/ai/chat/', {'messages': [{'role': 'user', 'content': 'What first?'}]}, format='json')
        self.assertEqual(r.json()['reply'], 'Start with the CCTV job.')
        self.assertIn('Amina secret job', captured['sent'])
        self.assertNotIn('Brian private job', captured['sent'])           # staff never leak teammates' work to the model
        self.assertEqual(len(self.am.get('/api/ai/history/').json()), 2)

        with mock.patch('base.ai.requests.post', fake_post):
            self.a.post('/api/ai/follow-up/')
        self.assertIn('Brian private job', captured['sent'])               # admin snapshot covers the whole team

    @override_settings(ANTHROPIC_API_KEY='test-key')
    def test_draft_document(self):
        with mock.patch('base.ai.requests.post', return_value=mock.Mock(status_code=200, json=lambda: {'content': [{'type': 'text', 'text': 'MINUTES\nAttendees'}]})):
            r = self.am.post('/api/ai/draft/', {'kind': 'meeting', 'title': 'Weekly', 'notes': 'we met'}, format='json')
        self.assertTrue(r.json()['reply'].startswith('MINUTES'))
