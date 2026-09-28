import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicIdModel(models.Model):
    """
    Internal PK stays BIGINT auto-increment (SRS 7.0). public_id is the
    identifier exposed in API URLs so internal row counts are never leaked.
    """

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)

    class Meta:
        abstract = True


class AuditLog(PublicIdModel, TimeStampedModel):
    """
    Immutable trail for sensitive actions (SRS 7.18): operator approval,
    refunds, booking cancellation, role changes. Written by
    apps.core.audit.log_action(), called from the relevant services.py —
    never from a view, so it can't be bypassed by whichever entry point
    triggers the action.
    """

    class Action(models.TextChoices):
        OPERATOR_APPROVED = "operator_approved", "Operator approved"
        OPERATOR_REJECTED = "operator_rejected", "Operator rejected"
        BOOKING_CANCELLED = "booking_cancelled", "Booking cancelled"
        REFUND_ISSUED = "refund_issued", "Refund issued"
        ROLE_CHANGED = "role_changed", "Role changed"
        # Step 15: a delay/cancel notifies every booked passenger, so who
        # did it (and when) must be traceable like the other sensitive actions.
        TRIP_STATUS_CHANGED = "trip_status_changed", "Trip status changed"
        # Step 16 (admin panel).
        OPERATOR_SUSPENDED = "operator_suspended", "Operator suspended"
        OPERATOR_REINSTATED = "operator_reinstated", "Operator reinstated"
        USER_LOCKED = "user_locked", "User locked"
        USER_UNLOCKED = "user_unlocked", "User unlocked"
        PAYOUT_RECORDED = "payout_recorded", "Operator payout recorded"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        help_text="Null for a system-triggered action (e.g. a payment webhook cancelling a booking).",
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    target_model = models.CharField(max_length=100)
    target_id = models.CharField(max_length=64, help_text="The target's public_id, as a string.")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_audit_log"
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        return f"{self.action}:{self.target_model}:{self.target_id}"
