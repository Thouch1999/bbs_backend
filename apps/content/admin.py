from django.contrib import admin

from .models import Announcement, Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("title_en", "placement", "is_active", "starts_at", "ends_at")
    list_filter = ("placement", "is_active")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("message_en", "severity", "audience", "is_active", "starts_at", "ends_at")
    list_filter = ("severity", "audience", "is_active")
