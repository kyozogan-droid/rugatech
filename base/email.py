"""Branded HTML emails (navy + gold to match the app). Sent in a background thread in production."""
import logging
import threading
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

NAVY, GOLD = '#06193B', '#4DA82A'   # Rugatech logo navy + leaf green


def _render(heading, paragraphs, code='', note='', urgent=False):
    accent = '#E5584F' if urgent else GOLD
    body = ''.join(f'<p style="margin:0 0 14px;font-size:15px;line-height:1.55;color:{NAVY}">{p}</p>' for p in paragraphs)
    code_box = (f'<div style="margin:22px 0;padding:18px;text-align:center;background:#EAF3FB;border-radius:12px;'
                f'font-size:34px;letter-spacing:10px;font-weight:700;color:{NAVY}">{escape(code)}</div>') if code else ''
    logo = (f'<img src="{escape(settings.EMAIL_LOGO_URL)}" width="30" height="30" alt="" style="vertical-align:middle;margin-right:10px;background:#ffffff;border-radius:8px;padding:3px">'
            if settings.EMAIL_LOGO_URL else '')
    note_html = f'<p style="margin:18px 0 0;font-size:12px;color:#8B96A3">{note}</p>' if note else ''
    return f'''<!doctype html><html><body style="margin:0;background:#F2F6FA;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="520" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #DCE5EF">
<tr><td style="background:{NAVY};padding:16px 24px">{logo}<span style="color:{accent};font-weight:700;letter-spacing:2px;font-size:12px;vertical-align:middle">{escape(settings.APP_NAME.upper())}</span></td></tr>
<tr><td style="padding:26px 24px"><h2 style="margin:0 0 16px;font-size:21px;color:{NAVY}">{heading}</h2>{body}{code_box}{note_html}</td></tr>
</table></td></tr></table></body></html>'''


def _deliver(subject, to, html, text):
    try:
        msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
    except Exception as e:
        logger.warning('Email to %s failed: %s', to, e)


def send_email(to, subject, heading, paragraphs, code='', note='', urgent=False):
    if not to:
        return
    html = _render(heading, paragraphs, code, note, urgent)
    text = '\n\n'.join([heading, *[p.replace('<b>', '').replace('</b>', '').replace('<br/>', '\n') for p in paragraphs], code]).strip()
    if getattr(settings, 'EMAIL_ASYNC', True):
        threading.Thread(target=_deliver, args=(subject, to, html, text), daemon=True).start()
    else:
        _deliver(subject, to, html, text)


def _first(user):
    return escape(user.first_name or 'there')


def send_verification_code(user, code):
    send_email(user.email, f'{code} is your {settings.APP_NAME} verification code', 'Verify your email',
               [f'Hi {_first(user)}, enter this code in the app to finish creating your account.'], code=code,
               note='The code expires in 15 minutes. If you did not sign up, you can ignore this email.')


def send_password_reset_code(user, code):
    send_email(user.email, f'{code} is your {settings.APP_NAME} password reset code', 'Reset your password',
               [f'Hi {_first(user)}, use this code in the app to choose a new password.'], code=code,
               note='The code expires in 15 minutes. If you did not ask for this, your password is unchanged.')


def send_task_assigned(user, task, assigned_by):
    when = task.deadline.strftime('%a %d %b %Y, %H:%M UTC')
    paras = [f'Hi {_first(user)},', f'<b>{escape(task.title)}</b>' + (f'<br/>{escape(task.description)}' if task.description else ''),
             f'Deadline: <b>{when}</b><br/>Estimated time: <b>{task.estimated_minutes} min</b> · Priority: <b>{task.priority}</b>']
    if assigned_by:
        paras.append(f'Assigned by {escape(assigned_by.get_full_name() or assigned_by.email)}.')
    send_email(user.email, f'New task: {task.title}', 'You have a new task', paras,
               note=f'You will be reminded 24 hours and 1 hour before the deadline. Open the {settings.APP_NAME} app to start.')


def send_reminder(user, title, body, urgent=False):
    send_email(user.email, title, title, [f'Hi {_first(user)},', escape(body)], urgent=urgent,
               note=f'Open the {settings.APP_NAME} app for details. You can turn email reminders off in Settings.')
