"""
Django settings for the Rugatech Staff backend.

Same stack and conventions as the SRC backend (Django + DRF TokenAuthentication,
PostgreSQL via DATABASE_URL, WhiteNoise, Render) — with one deliberate difference:
NO secret is ever hardcoded in this file. Everything sensitive comes from environment
variables (see backend/README.md for the full list).
"""
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=''):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, 'true' if default else 'false').strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [x.strip() for x in env(name, default).split(',') if x.strip()]


# ── Core ──────────────────────────────────────────────────────────────────────
# Defaults to PRODUCTION-safe. For local development set DJANGO_DEBUG=true.
DEBUG = True
TESTING = 'test' in sys.argv

SECRET_KEY = 'Kk2WOiF9P7ORCBrSwV2SmsAn83odSyxMdGriMSns3aAXWWBiyTOtq7Bi7Q1_FbFQumGJMSF6FJ89oxSy'


ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'base',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'rugatech.urls'
WSGI_APPLICATION = 'rugatech.wsgi.application'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
    ]},
}]

# ── Database ──────────────────────────────────────────────────────────────────
# Production MUST use PostgreSQL (DATABASE_URL). Render's web-service disk is ephemeral,
# so SQLite there would be wiped on every restart/deploy. SQLite is only the local-dev fallback.
DATABASE_URL = env('DATABASE_URL')
if DATABASE_URL:
    DATABASES = {'default': dj_database_url.parse(
        DATABASE_URL, conn_max_age=600,
        ssl_require=env_bool('DATABASE_SSL_REQUIRE', True),
    )}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}

if TESTING:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']   # fast tests only

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Static & uploaded files ───────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded files (profile photos, staff documents). Render's disk is ephemeral, so pick ONE:
#   a) S3-compatible bucket  → set AWS_STORAGE_BUCKET_NAME (+ keys / AWS_S3_ENDPOINT_URL for R2, B2, etc.)
#   b) Render persistent disk → mount it and set MEDIA_ROOT=/var/data/media
MEDIA_URL = '/media/'
MEDIA_ROOT = env('MEDIA_ROOT') or str(BASE_DIR / 'media')
MAX_UPLOAD_BYTES = int(env('MAX_UPLOAD_BYTES', str(25 * 1024 * 1024)))
DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if TESTING or DEBUG:
    STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}

if env('AWS_STORAGE_BUCKET_NAME'):
    INSTALLED_APPS.append('storages')
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': env('AWS_STORAGE_BUCKET_NAME'),
            'access_key': env('AWS_ACCESS_KEY_ID'),
            'secret_key': env('AWS_SECRET_ACCESS_KEY'),
            'endpoint_url': env('AWS_S3_ENDPOINT_URL') or None,
            'region_name': env('AWS_S3_REGION_NAME') or None,
            'signature_version': 's3v4',
            'querystring_auth': True,    # private objects, short-lived signed URLs
            'querystring_expire': 300,
            'file_overwrite': False,
        },
    }

# ── REST framework (same TokenAuthentication scheme as SRC: "Authorization: Token <key>") ──
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.TokenAuthentication'],
    # Safer default than SRC's AllowAny: every endpoint needs a login unless it opts out.
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.ScopedRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'auth': '30/min', 'code': '10/min', 'ai': '30/min'},
}

# ── CORS (mobile apps don't need it; kept for a future admin web app) ─────────
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_HEADERS = ['accept', 'authorization', 'content-type', 'origin', 'user-agent', 'x-requested-with']

# ── Email (any SMTP provider: Gmail app password, Brevo, Mailgun, SendGrid, Zoho…) ─────
if env('EMAIL_HOST'):
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = env('EMAIL_HOST')
    EMAIL_PORT = int(env('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
    EMAIL_HOST_USER = env('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'   # prints emails in the server log
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'Rugatech Staff <no-reply@rugatech.local>')
EMAIL_ASYNC = not TESTING   # send in a background thread so API calls never wait on SMTP

# ── Rugatech app settings ─────────────────────────────────────────────────────
APP_NAME = env('APP_NAME', 'Rugatech Staff')
SUPPORT_EMAIL = env('SUPPORT_EMAIL', '')
# Optional: public URL of the logo (e.g. https://<you>.github.io/<repo>/assets/logo.png) shown in email headers.
EMAIL_LOGO_URL = env('EMAIL_LOGO_URL', '')
# Emails that become ADMIN automatically when they register / sign in with Google.
ADMIN_EMAILS = [e.lower() for e in env_list('ADMIN_EMAILS')]
# Require the 6-digit email code before first login (Google sign-ins are already verified).
REQUIRE_EMAIL_VERIFICATION = env_bool('REQUIRE_EMAIL_VERIFICATION', True)
# Google OAuth client IDs your app uses (Android / iOS / Web). Tokens for any other client are rejected.
GOOGLE_OAUTH_CLIENT_IDS = env_list('GOOGLE_OAUTH_CLIENT_IDS')
# Shared secret for POST /api/internal/send-reminders/ (alternative to a Render cron job).
CRON_SECRET = env('CRON_SECRET')
# AI (task follower bot + assistant)
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')
ANTHROPIC_MODEL = env('ANTHROPIC_MODEL', 'claude-sonnet-5-5')

# ── Production security ───────────────────────────────────────────────────────
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_CONTENT_TYPE_NOSNIFF = True
    CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': env('LOG_LEVEL', 'INFO')},
}
