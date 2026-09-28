from django.contrib import admin

from .models import SupportMessage, SupportTicket


class SupportMessageInline(admin.TabularInline):
    model = SupportMessage
    extra = 0


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("subject", "category", "priority", "status", "contact_phone", "sla_due_at")
    list_filter = ("category", "priority", "status")
    inlines = [SupportMessageInline]
