from django.contrib import admin

from . import models

for m in (models.Profile, models.Task, models.TaskAssignment, models.DiaryEntry, models.TimeLog, models.Event,
          models.Document, models.StoredFile, models.Notification, models.ApprovalRecord, models.AuditLog, models.ReminderLog):
    admin.site.register(m)
