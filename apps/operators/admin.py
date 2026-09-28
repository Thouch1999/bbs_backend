from django.contrib import admin

from .models import Operator


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "commission_rate", "contact_phone", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "name_km", "contact_phone", "contact_email")
