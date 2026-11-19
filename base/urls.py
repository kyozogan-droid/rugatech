from django.urls import path

from . import ai_views, auth_views, content_views, internal_views, member_views, task_views, work_views

urlpatterns = [
    # ── Authentication (same naming as the SRC backend) ──
    path('auth/register/', auth_views.RegisterView.as_view(), name='register'),
    path('auth/verify-email/', auth_views.VerifyEmailView.as_view(), name='verify-email'),
    path('auth/resend-code/', auth_views.ResendCodeView.as_view(), name='resend-code'),
    path('auth/login/', auth_views.LoginView.as_view(), name='login'),
    path('auth/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('auth/google/callback/', auth_views.GoogleLoginView.as_view(), name='google-login'),
    path('auth/forgot-password/', auth_views.ForgotPasswordView.as_view(), name='forgot-password'),
    path('auth/reset-password/', auth_views.ResetPasswordView.as_view(), name='reset-password'),
    path('auth/user/', auth_views.MeView.as_view(), name='user-detail'),
    path('auth/user/change-password/', auth_views.ChangePasswordView.as_view(), name='change-password'),
    path('auth/user/delete-account/', auth_views.DeleteAccountView.as_view(), name='delete-account'),
    path('auth/profile/', auth_views.ProfileView.as_view(), name='profile'),
    path('auth/push-token/', auth_views.PushTokenView.as_view(), name='push-token'),
    path('auth/approvals/', member_views.MyApprovalsView.as_view(), name='my-approvals'),
    path('avatars/<int:user_id>/', auth_views.AvatarView.as_view(), name='avatar'),

    # ── Team (admin) ──
    path('members/', member_views.MemberListView.as_view(), name='members'),
    path('members/<int:user_id>/', member_views.MemberDetailView.as_view(), name='member-detail'),
    path('members/<int:user_id>/approve/', member_views.MemberApproveView.as_view(), name='member-approve'),
    path('members/<int:user_id>/approvals/', member_views.MemberApprovalsView.as_view(), name='member-approvals'),
    path('members/<int:user_id>/stats/', member_views.MemberStatsView.as_view(), name='member-stats'),

    # ── Tasks ──
    path('tasks/', task_views.TaskListView.as_view(), name='tasks'),
    path('tasks/<uuid:pk>/', task_views.TaskDetailView.as_view(), name='task-detail'),
    path('tasks/<uuid:pk>/status/', task_views.TaskStatusView.as_view(), name='task-status'),

    # ── Hours, progress, leaderboard, diary ──
    path('work/clock/', work_views.ClockView.as_view(), name='clock'),
    path('work/clock-in/', work_views.ClockInView.as_view(), name='clock-in'),
    path('work/clock-out/', work_views.ClockOutView.as_view(), name='clock-out'),
    path('work/week-stats/', work_views.MyWeekStatsView.as_view(), name='week-stats'),
    path('leaderboard/', work_views.LeaderboardView.as_view(), name='leaderboard'),
    path('diary/', work_views.DiaryView.as_view(), name='diary'),
    path('diary/<int:pk>/comment/', work_views.DiaryCommentView.as_view(), name='diary-comment'),

    # ── Calendar, documents, files ──
    path('events/', content_views.EventListView.as_view(), name='events'),
    path('events/<uuid:pk>/', content_views.EventDetailView.as_view(), name='event-detail'),
    path('documents/', content_views.DocumentListView.as_view(), name='documents'),
    path('documents/<uuid:pk>/', content_views.DocumentDetailView.as_view(), name='document-detail'),
    path('files/', content_views.FileListView.as_view(), name='files'),
    path('files/<uuid:pk>/', content_views.FileDetailView.as_view(), name='file-detail'),
    path('files/<uuid:pk>/link/', content_views.FileLinkView.as_view(), name='file-link'),
    path('files/download/<str:token>/', content_views.FileDownloadView.as_view(), name='file-download'),

    # ── Notifications ──
    path('notifications/', content_views.NotificationListView.as_view(), name='notifications'),
    path('notifications/read-all/', content_views.NotificationReadAllView.as_view(), name='notifications-read-all'),
    path('notifications/unread-count/', content_views.UnreadCountView.as_view(), name='notifications-unread'),
    path('notifications/<uuid:pk>/read/', content_views.NotificationReadView.as_view(), name='notification-read'),

    # ── AI ──
    path('ai/chat/', ai_views.ChatView.as_view(), name='ai-chat'),
    path('ai/follow-up/', ai_views.FollowUpView.as_view(), name='ai-follow-up'),
    path('ai/draft/', ai_views.DraftView.as_view(), name='ai-draft'),
    path('ai/history/', ai_views.HistoryView.as_view(), name='ai-history'),

    # ── Scheduler hook (a Render cron job is preferred: `python manage.py send_reminders`) ──
    path('internal/send-reminders/', internal_views.SendRemindersView.as_view(), name='send-reminders'),
]
