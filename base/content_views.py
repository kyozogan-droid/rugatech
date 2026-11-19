import mimetypes

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document, Event, Notification, StoredFile
from .serializers import (DocumentInputSerializer, EventInputSerializer, document_payload, event_payload,
                          file_payload, notification_payload)

# ── Events ────────────────────────────────────────────────────────────────────


class EventListView(APIView):
    def get(self, request):
        qs = Event.objects.all() if request.user.profile.is_admin else Event.objects.filter(Q(is_company=True) | Q(created_by=request.user))
        start, end = parse_datetime(request.query_params.get('from', '') or ''), parse_datetime(request.query_params.get('to', '') or '')
        if start:
            qs = qs.filter(starts_at__gte=start)
        if end:
            qs = qs.filter(starts_at__lt=end)
        return Response([event_payload(e) for e in qs[:500]])

    def post(self, request):
        ser = EventInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        is_company = bool(d['is_company']) and request.user.profile.is_admin   # only admins can publish company-wide
        e = Event.objects.create(title=d['title'].strip(), description=d['description'].strip(), kind=d['kind'],
                                 starts_at=d['starts_at'], ends_at=d.get('ends_at'), is_company=is_company, created_by=request.user)
        return Response(event_payload(e), status=status.HTTP_201_CREATED)


class EventDetailView(APIView):
    def delete(self, request, pk):
        e = get_object_or_404(Event, pk=pk)
        if e.created_by_id != request.user.id and not request.user.profile.is_admin:
            return Response({'error': 'You can only delete your own events.'}, status=status.HTTP_403_FORBIDDEN)
        e.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Documents (meeting minutes, reports, event write-ups) ─────────────────────

def _visible_docs(user):
    return Document.objects.all() if user.profile.is_admin else Document.objects.filter(Q(created_by=user) | Q(shared=True))


class DocumentListView(APIView):
    def get(self, request):
        return Response([document_payload(d) for d in _visible_docs(request.user)])

    def post(self, request):
        ser = DocumentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = Document.objects.create(created_by=request.user, **ser.validated_data)
        return Response(document_payload(d), status=status.HTTP_201_CREATED)


class DocumentDetailView(APIView):
    def get(self, request, pk):
        return Response(document_payload(get_object_or_404(_visible_docs(request.user), pk=pk)))

    def patch(self, request, pk):
        d = get_object_or_404(Document, pk=pk, created_by=request.user)
        ser = DocumentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        for k, v in ser.validated_data.items():
            setattr(d, k, v)
        d.save()
        return Response(document_payload(d))

    put = patch

    def delete(self, request, pk):
        get_object_or_404(Document, pk=pk, created_by=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Files ─────────────────────────────────────────────────────────────────────

DOWNLOAD_SALT = 'rugatech.file-download'


def _visible_files(user):
    qs = StoredFile.objects.select_related('owner')
    return qs if user.profile.is_admin else qs.filter(Q(owner=user) | Q(shared=True))


class FileListView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        return Response([file_payload(f) for f in _visible_files(request.user)[:300]])

    def post(self, request):
        up = request.FILES.get('file')
        if not up:
            return Response({'error': 'No file was uploaded.'}, status=status.HTTP_400_BAD_REQUEST)
        if up.size > settings.MAX_UPLOAD_BYTES:
            return Response({'error': f'Files must be under {settings.MAX_UPLOAD_BYTES // 1048576} MB.'}, status=status.HTTP_400_BAD_REQUEST)
        mime = (request.data.get('mime') or up.content_type or mimetypes.guess_type(up.name)[0] or '')[:100]
        category = 'photo' if mime.startswith('image/') else ('document' if mime else 'other')
        shared = str(request.data.get('shared', 'false')).lower() in ('1', 'true', 'yes')
        f = StoredFile.objects.create(owner=request.user, name=(request.data.get('name') or up.name)[:255], file=up,
                                      mime=mime, size=up.size, category=category, shared=shared)
        return Response(file_payload(f), status=status.HTTP_201_CREATED)


class FileDetailView(APIView):
    def patch(self, request, pk):
        f = get_object_or_404(StoredFile, pk=pk, owner=request.user)
        if 'shared' in request.data:
            f.shared = str(request.data['shared']).lower() in ('1', 'true', 'yes')
            f.save(update_fields=['shared'])
        return Response(file_payload(f))

    def delete(self, request, pk):
        f = get_object_or_404(StoredFile, pk=pk, owner=request.user)
        f.file.delete(save=False)
        f.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FileLinkView(APIView):
    """Returns a short-lived signed URL so the phone can open/preview a private file without sending auth headers."""

    def get(self, request, pk):
        f = get_object_or_404(_visible_files(request.user), pk=pk)
        token = signing.dumps({'f': str(f.id)}, salt=DOWNLOAD_SALT)
        return Response({'url': request.build_absolute_uri(f'/api/files/download/{token}/'), 'name': f.name, 'category': f.category})


class FileDownloadView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            data = signing.loads(token, salt=DOWNLOAD_SALT, max_age=600)
        except signing.BadSignature:
            raise Http404
        f = get_object_or_404(StoredFile, pk=data['f'])
        try:
            fh = default_storage.open(f.file.name, 'rb')
        except Exception:
            raise Http404
        resp = FileResponse(fh, content_type=f.mime or 'application/octet-stream')
        disposition = 'inline' if f.category == 'photo' or f.mime == 'application/pdf' else 'attachment'
        resp['Content-Disposition'] = f'{disposition}; filename="{f.name.encode("ascii", "ignore").decode() or "file"}"'
        resp['Cache-Control'] = 'private, max-age=0'
        return resp


# ── Notifications ─────────────────────────────────────────────────────────────

class NotificationListView(APIView):
    def get(self, request):
        return Response([notification_payload(n) for n in Notification.objects.filter(user=request.user)[:100]])


class NotificationReadView(APIView):
    def post(self, request, pk):
        Notification.objects.filter(user=request.user, pk=pk).update(is_read=True)
        return Response({'ok': True})


class NotificationReadAllView(APIView):
    def post(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'ok': True})


class UnreadCountView(APIView):
    def get(self, request):
        return Response({'count': Notification.objects.filter(user=request.user, is_read=False).count()})
