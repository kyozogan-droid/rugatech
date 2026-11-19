from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import email as mail
from .models import Task, TaskAssignment
from .permissions import IsAdmin
from .serializers import TaskInputSerializer, TaskStatusSerializer, assignment_payload, task_payload
from .services import audit, notify, notify_admins


def _tasks_for(user):
    qs = Task.objects.prefetch_related('assignments__user__profile').order_by('deadline')
    return qs if user.profile.is_admin else qs.filter(assignments__user=user).distinct()


def _staff_users(ids):
    users = list(User.objects.filter(pk__in=ids, is_active=True).select_related('profile'))
    if len(users) != len(set(ids)):
        return None
    return users


def _announce(task, users, by):
    for u in users:
        notify(u, 'New task assigned', f'{task.title} — due {task.deadline.strftime("%d %b %H:%M")} UTC', 'task', task.id)
        if u.profile.email_reminders:
            mail.send_task_assigned(u, task, by)


class TaskListView(APIView):
    def get_permissions(self):
        return [IsAdmin()] if self.request.method == 'POST' else super().get_permissions()

    def get(self, request):
        return Response([task_payload(t, request) for t in _tasks_for(request.user)])

    @transaction.atomic
    def post(self, request):
        ser = TaskInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        users = _staff_users(d['assignee_ids'])
        if users is None:
            return Response({'error': 'One or more assignees do not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        task = Task.objects.create(title=d['title'].strip(), description=d['description'].strip(), priority=d['priority'],
                                   deadline=d['deadline'], estimated_minutes=d['estimated_minutes'], created_by=request.user)
        TaskAssignment.objects.bulk_create([TaskAssignment(task=task, user=u) for u in users])
        transaction.on_commit(lambda: _announce(task, users, request.user))
        audit(request, 'task_created', task=str(task.id), assignees=len(users))
        task = Task.objects.prefetch_related('assignments__user__profile').get(pk=task.pk)
        return Response(task_payload(task, request), status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    def get_permissions(self):
        return [IsAdmin()] if self.request.method in ('PATCH', 'PUT', 'DELETE') else super().get_permissions()

    def get(self, request, pk):
        task = get_object_or_404(_tasks_for(request.user), pk=pk)
        return Response(task_payload(task, request))

    @transaction.atomic
    def patch(self, request, pk):
        task = get_object_or_404(Task, pk=pk)
        ser = TaskInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        users = _staff_users(d['assignee_ids'])
        if users is None:
            return Response({'error': 'One or more assignees do not exist.'}, status=status.HTTP_400_BAD_REQUEST)
        task.title, task.description = d['title'].strip(), d['description'].strip()
        task.priority, task.deadline, task.estimated_minutes = d['priority'], d['deadline'], d['estimated_minutes']
        task.save()
        keep = {u.id for u in users}
        task.assignments.exclude(user_id__in=keep).delete()
        existing = set(task.assignments.values_list('user_id', flat=True))
        added = [u for u in users if u.id not in existing]
        TaskAssignment.objects.bulk_create([TaskAssignment(task=task, user=u) for u in added])
        transaction.on_commit(lambda: _announce(task, added, request.user))
        task = Task.objects.prefetch_related('assignments__user__profile').get(pk=task.pk)
        return Response(task_payload(task, request))

    put = patch

    def delete(self, request, pk):
        get_object_or_404(Task, pk=pk).delete()
        audit(request, 'task_deleted', task=str(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskStatusView(APIView):
    """The ONLY way an assignee changes task state — the server owns the clock, so actual time can't be faked."""

    @transaction.atomic
    def post(self, request, pk):
        ser = TaskStatusSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new = ser.validated_data['status']
        try:
            a = TaskAssignment.objects.select_for_update().select_related('task', 'user__profile').get(task_id=pk, user=request.user)
        except TaskAssignment.DoesNotExist:
            return Response({'error': 'This task is not assigned to you.'}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        running = int((now - a.timer_started_at).total_seconds()) if a.timer_started_at else 0
        if new == 'in_progress':
            a.status, a.started_at = 'in_progress', a.started_at or now
            a.timer_started_at, a.completed_at, a.is_late = now, None, False
        elif new == 'todo':
            a.status, a.timer_started_at = 'todo', None
            a.actual_seconds += max(0, running)
            a.completed_at, a.is_late = None, False
        else:
            a.status, a.timer_started_at = 'done', None
            a.actual_seconds += max(0, running)
            a.completed_at, a.started_at = now, a.started_at or now
            a.is_late = now > a.task.deadline
        a.save()
        if new == 'done':
            who = request.user.get_full_name() or request.user.email
            notify_admins('Task finished late' if a.is_late else 'Task completed', f'{who} finished "{a.task.title}"', 'task', a.task_id)
        return Response(assignment_payload(a, request))
