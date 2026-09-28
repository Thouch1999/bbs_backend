from django.contrib import admin

from .models import Payment, PromoCode, Refund, WebhookLog


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("booking", "provider", "status", "amount", "currency", "provider_txn_id", "created_at")
    list_filter = ("provider", "status")
    search_fields = ("booking__pnr", "provider_txn_id")
    inlines = [RefundInline]


@admin.register(WebhookLog)
class WebhookLogAdmin(admin.ModelAdmin):
    list_display = ("provider", "signature_valid", "payment", "created_at")
    list_filter = ("provider", "signature_valid")
    readonly_fields = ("provider", "raw_body", "headers", "signature_valid", "payment", "note", "created_at")


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "is_active", "valid_from", "valid_until")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)
