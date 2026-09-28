from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel
from apps.core.validators import validate_licence_document


def licence_upload_path(instance, filename):
    return f"operators/{instance.public_id}/licence/{filename}"


class Operator(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=150)
    name_km = models.CharField(max_length=150, blank=True)
    licence_number = models.CharField(max_length=50, blank=True)
    licence_document = models.FileField(
        upload_to=licence_upload_path,
        null=True,
        blank=True,
        validators=[validate_licence_document],
    )
    contact_phone = models.CharField(max_length=16)
    contact_email = models.EmailField(null=True, blank=True)
    address = models.CharField(max_length=255, blank=True)

    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=10, help_text="Percent, e.g. 10.00 = 10%"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    rejection_reason = models.CharField(max_length=255, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    # A2 document checklist: {"licence_number": "verified" | "needs_info" |
    # "rejected", ...} — the admin's per-document verdicts.
    review_checklist = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operators_approved",
    )

    class Meta:
        db_table = "operators_operator"

    def __str__(self):
        return self.name



class OperatorReviewNote(PublicIdModel, TimeStampedModel):
    """A2 timeline: internal admin notes on an application, plus "please
    send more information" requests, which the operator's owner can see."""

    class Kind(models.TextChoices):
        NOTE = "note", "Internal note"
        INFO_REQUEST = "info_request", "Information requested"

    operator = models.ForeignKey(Operator, on_delete=models.CASCADE, related_name="review_notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.NOTE)
    body = models.TextField()

    class Meta:
        db_table = "operators_review_note"
        ordering = ["-created_at"]
