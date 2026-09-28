from django.contrib import admin

from .models import BoardingRecord


@admin.register(BoardingRecord)
class BoardingRecordAdmin(admin.ModelAdmin):
    list_display = ("booking", "passenger", "status", "scanned_by", "scanned_at")
    list_filter = ("status",)
    search_fields = ("booking__pnr",)
