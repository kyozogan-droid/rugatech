# Rugatech Staff — Backend (Django + DRF)

Same stack and conventions as the SRC backend: **Django 6, Django REST Framework `TokenAuthentication`
(`Authorization: Token <key>`), PostgreSQL via `DATABASE_URL`, WhiteNoise, gunicorn, Render.**
Differences, on purpose: **no secret is hardcoded** (everything is an environment variable), endpoints require
login by default, and Google tokens are verified for *your* app before being trusted.

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DJANGO_DEBUG=true ADMIN_EMAILS=you@rugatech.com    # see .env.example for everything else
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
python manage.py test base                                # 43 tests
```

With no `EMAIL_HOST` set, emails (including verification codes) are printed in the server console.
The Android emulator reaches your computer at `http://10.0.2.2:8000/api`.

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DJANGO_SECRET_KEY` | **yes** (prod) | Signing key. Generate: `python -c "import secrets;print(secrets.token_urlsafe(60))"`. The server refuses to start without it. |
| `DJANGO_DEBUG` | no | `true` only for local development. Default `false`. |
| `DATABASE_URL` | **yes** (prod) | PostgreSQL URL. Without it SQLite is used (wiped on Render restarts). |
| `DJANGO_ALLOWED_HOSTS` | no | Comma list. Default allows `.onrender.com`. |
| `ADMIN_EMAILS` | **yes** | Comma list. These emails become **admin** when they register or sign in with Google. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | for real emails | Any SMTP provider (Brevo, Gmail app-password, Mailgun, Zoho…). |
| `GOOGLE_OAUTH_CLIENT_IDS` | for Google login | Comma list of the Android/iOS/Web client IDs the app uses. Tokens issued to any other client are rejected. |
| `ANTHROPIC_API_KEY` (`ANTHROPIC_MODEL`) | for AI | Task follower bot, assistant and document drafting. |
| `CRON_SECRET` | if using the HTTP scheduler | Protects `POST /api/internal/send-reminders/`. |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL` | for file storage | Any S3-compatible bucket (AWS S3, Cloudflare R2, Backblaze B2). **Needed on Render**, whose disk is wiped on every deploy. Alternative: attach a Render Disk and set `MEDIA_ROOT=/var/data/media`. |
| `EMAIL_LOGO_URL` | no | Public URL of the logo to show in email headers (the download site hosts one at `assets/logo.png`). |
| `REQUIRE_EMAIL_VERIFICATION` | no | Default `true`: new accounts must enter the 6-digit emailed code. |

## Deploy on Render

1. Push the repo to GitHub. Render → **New → Blueprint** → select it (uses `backend/render.yaml`; move it to the repo root if needed).
2. Fill the `sync: false` secrets when prompted. The build runs `build.sh` (install, collectstatic, migrate).
3. Make yourself admin: register in the app with an `ADMIN_EMAILS` address, or run `python manage.py create_admin you@rugatech.com` in the Render shell.
4. **Reminders** — pick one:
   - *Cron job* (defined in `render.yaml`; paid on Render): `python manage.py send_reminders` every 10 minutes.
   - *Free alternative:* an external pinger (e.g. cron-job.org) calls `POST https://<your-api>/api/internal/send-reminders/` with header `X-Cron-Secret: <CRON_SECRET>` every 10 minutes.

   Each reminder (24 h / 1 h / overdue) is sent exactly once per person, so extra runs are harmless.

Free Render services sleep when idle (first request takes ~30 s) and the free Postgres expires after 30 days. Use paid plans for a real team.

## What the API does

| Area | Endpoints |
|---|---|
| Auth | `auth/register/`, `auth/verify-email/`, `auth/resend-code/`, `auth/login/`, `auth/logout/`, `auth/google/callback/`, `auth/forgot-password/`, `auth/reset-password/`, `auth/user/` (+ `change-password/`, `delete-account/`), `auth/profile/` (registration form + photo), `auth/push-token/` |
| Team (admin) | `members/`, `members/<id>/`, `members/<id>/approve/`, `members/<id>/approvals/`, `members/<id>/stats/` |
| Tasks | `tasks/` (admin creates; staff see only their own), `tasks/<id>/`, `tasks/<id>/status/` (server-side timer) |
| Hours and progress | `work/clock/`, `work/clock-in/`, `work/clock-out/`, `work/week-stats/`, `leaderboard/` |
| Diary | `diary/`, `diary/<id>/comment/` (admin feedback) |
| Calendar, docs, files | `events/`, `documents/`, `files/` (+ short-lived signed download links) |
| Notifications | `notifications/`, `notifications/unread-count/`, `notifications/read-all/` |
| AI | `ai/chat/`, `ai/follow-up/`, `ai/draft/`, `ai/history/` |

**Security design:** staff never see other staff's tasks, diaries or private details; only admins can create tasks, approve members or read
registration details; task time can only be changed by the server; files are private and opened through 10-minute signed links; login is
rate-limited and locks after repeated failures; verification and reset codes are hashed, expire in 15 minutes and lock after 5 wrong tries;
password resets sign every device out; account deletion is available in-app.

## Rotate the secrets in the SRC backend

The `settings.py` from the SRC backend you uploaded has credentials committed in plain text (Django `SECRET_KEY`, a Google OAuth client
secret, an SMTP password and others). Anyone who has that file can use them. Rotate them (new Django key, new Google client secret, new email
app password), move them to environment variables, and remove them from git history. This backend is built so that can't happen.
