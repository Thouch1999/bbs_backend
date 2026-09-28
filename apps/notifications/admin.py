from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("channel", "template", "recipient", "language", "status", "retry_count", "created_at")
    list_filter = ("channel", "template", "status", "language")
    search_fields = ("recipient", "booking__pnr")
    readonly_fields = ("payload", "provider_response", "created_at", "updated_at")
