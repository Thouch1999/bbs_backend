from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class BoardingRecord(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        BOARDED = "boarded", "Boarded"
        NO_SHOW = "no_show", "No show"

    booking = models.ForeignKey(
        "bookings.Booking", on_delete=models.PROTECT, related_name="boarding_records"
    )
    passenger = models.ForeignKey(
        "bookings.BookingPassenger", on_delete=models.PROTECT, related_name="boarding_records"
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="boarding_scans",
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOARDED)

    class Meta:
        db_table = "boarding_boarding_record"
        constraints = [
            # A passenger can only ever be recorded once — this is what makes
            # a re-scan of an already-boarded ticket a clean, race-safe reject
            # rather than a duplicate row.
            models.UniqueConstraint(fields=["passenger"], name="unique_passenger_boarding_record"),
        ]
        indexes = [models.Index(fields=["booking"])]

    def __str__(self):
        return f"{self.booking.pnr}:{self.passenger_id} ({self.status})"
