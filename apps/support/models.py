"""Support & disputes queue (SRS 3.3, A5)."""

from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class SupportTicket(PublicIdModel, TimeStampedModel):
    class Category(models.TextChoices):
        REFUND_DISPUTE = "refund_dispute", "Refund dispute"
        PAYMENT_FAILED = "payment_failed", "Payment failed"
        TRIP_COMPLAINT = "trip_complaint", "Trip complaint"
        ACCOUNT = "account", "Account problem"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        WAITING_CUSTOMER = "waiting_customer", "Waiting for customer"
        RESOLVED = "resolved", "Resolved"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_tickets",
    )
    booking = models.ForeignKey(
        "bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="support_tickets"
    )
    contact_phone = models.CharField(max_length=16)
    contact_name = models.CharField(max_length=150, blank=True)
    language = models.CharField(max_length=2, default="km", help_text="Replies are written in this language.")
    category = models.CharField(max_length=20, choices=Category.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    subject = models.CharField(max_length=200)
    sla_due_at = models.DateTimeField()
    last_message_at = models.DateTimeField()
    unread_by_staff = models.BooleanField(default=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "support_ticket"
        indexes = [models.Index(fields=["status", "sla_due_at"]), models.Index(fields=["category"])]
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.category}: {self.subject}"


class SupportMessage(PublicIdModel, TimeStampedModel):
    class Sender(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        STAFF = "staff", "Staff"

    class Channel(models.TextChoices):
        WEB = "web", "Website"
        SMS = "sms", "SMS"
        TELEGRAM = "telegram", "Telegram"
        EMAIL = "email", "Email"

    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    sender = models.CharField(max_length=10, choices=Sender.choices)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.WEB)
    body = models.TextField()

    class Meta:
        db_table = "support_message"
        ordering = ["created_at"]
