"""Expo push notifications. Silent no-op for users without a registered device token."""
import logging
import threading

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
EXPO_URL = 'https://exp.host/--/api/v2/push/send'


def _post(messages):
    try:
        requests.post(EXPO_URL, json=messages, timeout=10, headers={'Accept': 'application/json'})
    except Exception as e:  # never let push problems break a request
        logger.warning('Expo push failed: %s', e)


def send_push(tokens, title, body, data=None):
    messages = [{'to': t, 'title': title, 'body': body, 'sound': 'default', 'data': data or {}}
                for t in tokens if t and t.startswith(('ExponentPushToken', 'ExpoPushToken'))]
    if not messages:
        return
    if getattr(settings, 'EMAIL_ASYNC', True):
        threading.Thread(target=_post, args=(messages,), daemon=True).start()
    else:
        logger.info('push (sync/test): %s', messages)
