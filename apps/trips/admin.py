from django.contrib import admin

from .models import Trip, TripSeat


class TripSeatInline(admin.TabularInline):
    model = TripSeat
    extra = 0
    readonly_fields = ("seat", "status", "held_until", "held_by_user", "held_by_session_key")
    can_delete = False


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("__str__", "route", "bus", "departure_at", "status", "seats_available")
    list_filter = ("status", "route__operator")
    date_hierarchy = "departure_at"
    inlines = [TripSeatInline]
