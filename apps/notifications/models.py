from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class Notification(PublicIdModel, TimeStampedModel):
    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        TELEGRAM = "telegram", "Telegram"

    class Language(models.TextChoices):
        KM = "km", "Khmer"
        EN = "en", "English"

    class Template(models.TextChoices):
        BOOKING_CONFIRMATION = "booking_confirmation", "Booking confirmation"
        PAYMENT_RECEIPT = "payment_receipt", "Payment receipt"
        DEPARTURE_REMINDER = "departure_reminder", "Departure reminder"
        TRIP_DELAYED = "trip_delayed", "Trip delayed"
        TRIP_CANCELLED = "trip_cancelled", "Trip cancelled"
        REFUND_PROCESSED = "refund_processed", "Refund processed"
        SUPPORT_REPLY = "support_reply", "Support reply"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.CharField(
        max_length=255, help_text="Phone (E.164), email address, or Telegram chat id."
    )
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.KM)
    template = models.CharField(max_length=30, choices=Template.choices)
    payload = models.JSONField(default=dict, blank=True, help_text="Template render context.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_response = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)

    # Both optional and independent: a departure reminder has a trip but no
    # payment; a refund notice has neither seats nor a fresh booking action.
    booking = models.ForeignKey(
        "bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )
    trip = models.ForeignKey(
        "trips.Trip", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )

    class Meta:
        db_table = "notifications_notification"
        indexes = [
            # Dedup check for the departure-reminder beat task: "has this
            # booking already had this template sent?"
            models.Index(fields=["template", "booking"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.channel}:{self.template}:{self.recipient} ({self.status})"
