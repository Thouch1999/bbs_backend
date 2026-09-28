from django.conf import settings
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class Booking(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    class Channel(models.TextChoices):
        WEB = "web", "Web"
        COUNTER = "counter", "Counter"
        TELEGRAM = "telegram", "Telegram"

    class Currency(models.TextChoices):
        KHR = "KHR", "Khmer Riel"
        USD = "USD", "US Dollar"

    pnr = models.CharField(max_length=8, unique=True, db_index=True)
    trip = models.ForeignKey("trips.Trip", on_delete=models.PROTECT, related_name="bookings")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings"
    )
    contact_phone = models.CharField(max_length=16, db_index=True)
    contact_email = models.EmailField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    booking_channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.WEB)
    booked_by_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bookings_created_as_staff",
    )

    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.USD)
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fee_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    promo_code = models.CharField(max_length=30, blank=True)

    boarding_stop = models.ForeignKey(
        "routes.RouteStop", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    drop_stop = models.ForeignKey(
        "routes.RouteStop", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    qr_token = models.TextField(blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    refund_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    previous_trip = models.ForeignKey(
        "trips.Trip", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rescheduled_at = models.DateTimeField(null=True, blank=True)
    reschedule_fee_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    fare_difference_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "bookings_booking"
        indexes = [
            models.Index(fields=["pnr", "contact_phone"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["trip"]),
        ]

    def __str__(self):
        return self.pnr


class BookingPassenger(PublicIdModel, TimeStampedModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="passengers")
    # A ForeignKey, not OneToOneField: a TripSeat's earlier booking may have
    # been cancelled (or its payment failed) — cancel_booking correctly
    # flips TripSeat back to AVAILABLE for a new hold, but never deletes the
    # old BookingPassenger row, so a OneToOneField here would mean that seat
    # could then never be booked again on this trip (IntegrityError on the
    # very next successful create_booking for it). Uniqueness of the
    # *currently active* passenger per seat is already enforced by
    # TripSeat.status transitions under row locks (trips.services.hold_seats
    # etc.), not by this FK — see apps/boarding/services.py's get_manifest
    # for how the "currently active" row is picked out.
    trip_seat = models.ForeignKey(
        "trips.TripSeat", on_delete=models.PROTECT, related_name="booking_passenger"
    )
    full_name = models.CharField(max_length=150)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    phone = models.CharField(max_length=16, blank=True)
    id_document_number = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "bookings_booking_passenger"

    def __str__(self):
        return self.full_name
