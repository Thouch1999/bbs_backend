from django.contrib import admin

from .models import Bus, Seat, SeatLayout


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0
    ordering = ("deck", "row_position", "col_position")


@admin.register(SeatLayout)
class SeatLayoutAdmin(admin.ModelAdmin):
    list_display = ("name", "operator", "bus_type", "deck_count")
    list_filter = ("operator", "bus_type")
    inlines = [SeatInline]


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("plate_number", "operator", "seat_layout", "status")
    list_filter = ("operator", "status")
    search_fields = ("plate_number",)
