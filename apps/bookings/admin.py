from django.contrib import admin

from .models import Booking, BookingPassenger


class BookingPassengerInline(admin.TabularInline):
    model = BookingPassenger
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("pnr", "trip", "status", "booking_channel", "currency", "total_amount", "created_at")
    list_filter = ("status", "booking_channel", "currency")
    search_fields = ("pnr", "contact_phone", "contact_email")
    inlines = [BookingPassengerInline]
