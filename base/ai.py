"""Task follower bot + assistant (Claude via the Anthropic API). The key lives only on the server."""
import requests
from django.conf import settings
from django.utils import timezone

from .models import AIMessage, TaskAssignment

TEMPLATES = {
    'meeting': 'Meeting minutes. Sections: Title/Date, Attendees, Agenda, Discussion summary, Decisions made, Action items (owner — task — due date), Next meeting.',
    'report': 'Professional work report. Sections: Summary, Progress against tasks, Achievements, Challenges/blockers, Hours & time notes, Next steps.',
    'event': 'Event write-up. Sections: Overview, Purpose, Date/venue/attendees, What happened, Outcomes, Follow-up actions, Lessons learned.',
}
PERSONA = ('You are the Rugatech Staff assistant inside a staff productivity app. Be concise, warm and practical. '
           'Use plain text with short lists; no markdown headings unless drafting a document. '
           'Never invent tasks or data that are not in the snapshot.')


class AIError(Exception):
    pass


def _claude(system, messages, max_tokens=1200):
    if not settings.ANTHROPIC_API_KEY:
        raise AIError('The AI assistant is not configured yet (ANTHROPIC_API_KEY is missing on the server).')
    try:
        r = requests.post('https://api.anthropic.com/v1/messages', timeout=60, headers={
            'x-api-key': settings.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': settings.ANTHROPIC_MODEL, 'max_tokens': max_tokens, 'system': system, 'messages': messages})
    except requests.RequestException:
        raise AIError('Could not reach the AI service. Please try again.')
    if r.status_code != 200:
        raise AIError(f'AI service error ({r.status_code}).')
    return ''.join(b.get('text', '') for b in r.json().get('content', []) if b.get('type') == 'text').strip()


def snapshot(user):
    """Compact, role-aware picture of open work. Staff see only their own tasks; admins see the team's."""
    is_admin = user.profile.is_admin
    qs = TaskAssignment.objects.exclude(status='done').select_related('task', 'user')
    if not is_admin:
        qs = qs.filter(user=user)
    now = timezone.now()
    lines = []
    for a in qs.order_by('task__deadline')[:80]:
        flag = ', OVERDUE' if a.task.deadline < now else ''
        who = f'{a.user.get_full_name() or a.user.email}: ' if is_admin else ''
        lines.append(f'- {who}{a.task.title} [{a.status}{flag}] due {a.task.deadline.isoformat()} est {a.task.estimated_minutes}m, '
                     f'logged {round(a.actual_seconds / 60)}m, {a.task.priority} priority')
    return (f'Current time: {now.isoformat()}\nUser: {user.get_full_name()} ({user.profile.role}'
            f'{", " + user.profile.job_title if user.profile.job_title else ""})\n'
            f'Open tasks{" across the team" if is_admin else ""}:\n' + ('\n'.join(lines) or '(none)'))


def follow_up(user):
    role = ('Tell the admin who is at risk or overdue and what to chase first (max 5 bullets).' if user.profile.is_admin else
            'Give this staff member a short prioritised plan for today and a friendly nudge about anything overdue or due soon (max 5 bullets).')
    return _claude(f'{PERSONA}\nYou are the Task Follower Bot. {role}', [{'role': 'user', 'content': snapshot(user)}], 500)


def draft(user, kind, title, notes, existing=None):
    kind = kind if kind in TEMPLATES else 'report'
    prompt = (f'Write a {TEMPLATES[kind]}\nTitle: {title or "(untitled)"}\nRaw notes from the user:\n{notes or "(none provided)"}\n'
              + (f'\nExisting draft to improve:\n{existing}\n' if existing else '')
              + f'\nContext (use only if relevant to the notes):\n{snapshot(user)}\n\n'
              'Output the finished document in clean plain text with clear section labels. Do not add commentary before or after it. '
              'Mark anything missing as "[to be confirmed]".')
    return _claude(PERSONA, [{'role': 'user', 'content': prompt}], 1800)


def chat(user, messages):
    history = [{'role': 'assistant' if m.get('role') == 'assistant' else 'user', 'content': str(m.get('content', ''))[:4000]}
               for m in messages[-12:] if m.get('content')]
    if not history or history[-1]['role'] != 'user':
        raise AIError('No message to answer.')
    system = (f'{PERSONA}\nYou help with: tracking tasks and deadlines, writing meeting minutes, event write-ups and reports, '
              f'and planning the workday.\n\n{snapshot(user)}')
    reply = _claude(system, history)
    AIMessage.objects.bulk_create([AIMessage(user=user, role='user', content=history[-1]['content']),
                                   AIMessage(user=user, role='assistant', content=reply)])
    return reply
