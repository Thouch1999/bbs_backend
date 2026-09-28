from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import OperatorStaff, SavedPassenger, User


@admin.register(User)
class BBMSUserAdmin(UserAdmin):
    model = User
    list_display = ("email", "phone", "role", "is_active", "is_staff")
    search_fields = ("email", "phone", "full_name")
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Profile",
            {
                "fields": (
                    "full_name",
                    "phone",
                    "role",
                    "preferred_language",
                    "preferred_currency",
                )
            },
        ),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )


@admin.register(OperatorStaff)
class OperatorStaffAdmin(admin.ModelAdmin):
    list_display = ("user", "operator", "staff_role", "can_create_bookings", "can_validate_tickets")
    list_filter = ("staff_role", "operator")
    search_fields = ("user__phone", "user__full_name")


@admin.register(SavedPassenger)
class SavedPassengerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "phone", "gender")
    search_fields = ("full_name", "phone", "user__phone")
