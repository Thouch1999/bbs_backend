from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class Trip(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        DELAYED = "delayed", "Delayed"
        CANCELLED = "cancelled", "Cancelled"
        DEPARTED = "departed", "Departed"
        COMPLETED = "completed", "Completed"

    route = models.ForeignKey("routes.Route", on_delete=models.PROTECT, related_name="trips")
    bus = models.ForeignKey("fleet.Bus", on_delete=models.PROTECT, related_name="trips")
    departure_at = models.DateTimeField(help_text="UTC.")
    arrival_at = models.DateTimeField(help_text="Estimated, UTC.")
    base_fare_usd = models.DecimalField(max_digits=8, decimal_places=2)
    base_fare_khr = models.DecimalField(max_digits=12, decimal_places=0)
    seats_available = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.SCHEDULED)
    delay_minutes = models.PositiveIntegerField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)

    # Crew assignment (playbook Step 15, O2). Drivers don't log in to BBMS,
    # so they're plain name/phone fields; the conductor is a staff account
    # (they scan tickets), so it's a real FK.
    driver_name = models.CharField(max_length=150, blank=True)
    driver_phone = models.CharField(max_length=16, blank=True)
    conductor = models.ForeignKey(
        "accounts.OperatorStaff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conducted_trips",
    )

    class Meta:
        db_table = "trips_trip"
        indexes = [
            models.Index(fields=["route", "departure_at"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["departure_at"]

    def __str__(self):
        return f"{self.route_id} @ {self.departure_at:%Y-%m-%d %H:%M}"


class TripSeat(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        HELD = "held", "Held"
        BOOKED = "booked", "Booked"

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="trip_seats")
    seat = models.ForeignKey("fleet.Seat", on_delete=models.PROTECT, related_name="trip_seats")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AVAILABLE)
    held_until = models.DateTimeField(null=True, blank=True)
    held_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="held_trip_seats",
    )
    held_by_session_key = models.CharField(
        max_length=40, blank=True, help_text="Guest hold, when held_by_user is null."
    )

    class Meta:
        db_table = "trips_trip_seat"
        constraints = [
            models.UniqueConstraint(fields=["trip", "seat"], name="unique_trip_seat"),
        ]
        indexes = [
            models.Index(fields=["trip", "status"]),
            models.Index(fields=["status", "held_until"]),
        ]

    def __str__(self):
        return f"{self.trip_id}:{self.seat_id} ({self.status})"
