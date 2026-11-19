from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ai
from .models import AIMessage


class _AIView(APIView):
    throttle_scope = 'ai'

    def run(self, fn, *args):
        try:
            return Response({'reply': fn(*args)})
        except ai.AIError as e:
            return Response({'error': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class ChatView(_AIView):
    def post(self, request):
        msgs = request.data.get('messages')
        if not isinstance(msgs, list):
            return Response({'error': 'messages must be a list'}, status=status.HTTP_400_BAD_REQUEST)
        return self.run(ai.chat, request.user, msgs)


class FollowUpView(_AIView):
    def post(self, request):
        return self.run(ai.follow_up, request.user)


class DraftView(_AIView):
    def post(self, request):
        d = request.data
        return self.run(ai.draft, request.user, d.get('kind'), str(d.get('title', ''))[:200], str(d.get('notes', ''))[:8000],
                        (str(d['existing'])[:12000] if d.get('existing') else None))


class HistoryView(APIView):
    def get(self, request):
        rows = list(AIMessage.objects.filter(user=request.user).order_by('-created_at')[:30])[::-1]
        return Response([{'role': m.role, 'content': m.content, 'created_at': m.created_at.isoformat()} for m in rows])
