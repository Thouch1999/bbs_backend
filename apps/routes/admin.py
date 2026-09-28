from django.contrib import admin

from .models import City, Route, RouteStop, Stop


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name_en", "name_km", "country_code", "is_active")
    search_fields = ("name_en", "name_km")
    list_filter = ("country_code", "is_active")


@admin.register(Stop)
class StopAdmin(admin.ModelAdmin):
    list_display = ("name_en", "city", "is_active")
    search_fields = ("name_en", "name_km", "address")
    list_filter = ("city", "is_active")


class RouteStopInline(admin.TabularInline):
    model = RouteStop
    extra = 0
    ordering = ("sequence",)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "operator", "origin_city", "destination_city", "is_active")
    list_filter = ("operator", "is_active")
    search_fields = ("name",)
    inlines = [RouteStopInline]
