"""
Admin-managed marketing/notice content (SRS 3.3, A4). Every user-facing
text field exists once per language — the site shows one language at a
time (CLAUDE.md), so km and en copy are stored separately, never stacked.
"""

from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.core.validators import validate_banner_image


def banner_image_upload_path(instance, filename):
    return f"content/banners/{instance.public_id}/{filename}"


class ScheduledContent(PublicIdModel, TimeStampedModel):
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Empty = visible immediately.")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Empty = no end.")
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Banner(ScheduledContent):
    class Placement(models.TextChoices):
        HOME = "home", "Home page"
        SEARCH = "search", "Search results"
        MY_BOOKINGS = "my_bookings", "My bookings"

    title_km = models.CharField(max_length=120)
    title_en = models.CharField(max_length=120)
    body_km = models.CharField(max_length=255, blank=True)
    body_en = models.CharField(max_length=255, blank=True)
    placement = models.CharField(max_length=15, choices=Placement.choices, default=Placement.HOME)
    background_color = models.CharField(max_length=7, default="#0D5F6E", help_text="Hex, e.g. #0D5F6E.")
    image = models.ImageField(
        upload_to=banner_image_upload_path,
        null=True,
        blank=True,
        validators=[validate_banner_image],
        help_text="Optional. Falls back to background_color when empty.",
    )
    link_url = models.CharField(max_length=255, blank=True, help_text="Site-relative path or full URL.")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "content_banner"
        ordering = ["placement", "sort_order", "-created_at"]

    def __str__(self):
        return self.title_en


class Announcement(ScheduledContent):
    class Severity(models.TextChoices):
        INFO = "info", "Information"
        WARNING = "warning", "Warning"

    class Audience(models.TextChoices):
        ALL = "all", "Everyone"
        PASSENGERS = "passengers", "Passengers"
        OPERATORS = "operators", "Operators"

    message_km = models.CharField(max_length=500)
    message_en = models.CharField(max_length=500)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO)
    audience = models.CharField(max_length=12, choices=Audience.choices, default=Audience.ALL)

    class Meta:
        db_table = "content_announcement"
        ordering = ["-created_at"]

    def __str__(self):
        return self.message_en[:50]
