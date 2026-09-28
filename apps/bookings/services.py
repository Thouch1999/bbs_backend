"""
Booking lifecycle: create (from a seat hold) -> confirm (after payment) ->
cancel or reschedule. Fare math and the cancellation policy live here, not
in views/serializers, per CLAUDE.md's hard rules.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.services import normalize_phone
from apps.core.audit import log_action
from apps.core.models import AuditLog
from apps.core.utils import generate_pnr, sign_qr_token, to_khr
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat

from .models import Booking, BookingPassenger


class BookingError(Exception):
    code = "booking_error"


class InvalidPassengerCountError(BookingError):
    code = "passenger_count_mismatch"


class BookingStateError(BookingError):
    code = "booking_state_error"


def _quantize(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _fare_per_seat(trip: Trip, currency: str) -> Decimal:
    return trip.base_fare_usd if currency == Booking.Currency.USD else trip.base_fare_khr


def estimate_subtotal(trip: Trip, currency: str, num_seats: int) -> Decimal:
    """Public so payments.services can preview a promo code's discount before booking."""
    return _quantize(_fare_per_seat(trip, currency) * num_seats)


def _booking_fee(currency: str) -> Decimal:
    fee_usd = Decimal(str(settings.BOOKING_FEE_USD))
    return fee_usd if currency == Booking.Currency.USD else to_khr(fee_usd)


def _reschedule_fee(currency: str) -> Decimal:
    fee_usd = Decimal(str(settings.RESCHEDULE_FEE_USD))
    return fee_usd if currency == Booking.Currency.USD else to_khr(fee_usd)


def _refund_percent_for(hours_before_departure: Decimal) -> Decimal:
    tiers = sorted(
        settings.CANCELLATION_REFUND_TIERS, key=lambda t: t["min_hours_before_departure"], reverse=True
    )
    for tier in tiers:
        if hours_before_departure >= tier["min_hours_before_departure"]:
            return Decimal(tier["refund_percent"])
    return Decimal(0)


def _exists_pnr(code: str) -> bool:
    return Booking.objects.filter(pnr=code).exists()


@transaction.atomic
def create_booking(
    trip_id,
    hold_token: str,
    passengers: list[dict],
    *,
    contact_phone: str,
    contact_email: str | None = None,
    user=None,
    currency: str = Booking.Currency.USD,
    booking_channel: str = Booking.Channel.WEB,
    booked_by_staff=None,
    boarding_stop_id=None,
    drop_stop_id=None,
    discount_amount: Decimal = Decimal(0),
    promo_code: str = "",
) -> Booking:
    """
    Turns a still-valid seat hold into a pending_payment booking. Does NOT
    touch TripSeat state — the seats stay HELD until confirm_booking() is
    called after payment succeeds, so an abandoned pending_payment booking
    is cleaned up by the same expiry sweep as any other unconverted hold.
    """
    payload = trip_services.verify_hold_token(hold_token)  # raises InvalidHoldTokenError if tampered/expired
    if payload["trip_id"] != trip_id:
        raise trip_services.InvalidHoldTokenError(_("This hold token is for a different trip."))

    seat_ids = payload["seat_ids"]
    if len(passengers) != len(seat_ids):
        raise InvalidPassengerCountError(
            _("%(passengers)s passengers given for %(seats)s held seats.")
            % {"passengers": len(passengers), "seats": len(seat_ids)}
        )

    trip = Trip.objects.select_related("route").get(id=trip_id)
    trip_seats = list(
        TripSeat.objects.filter(trip_id=trip_id, seat_id__in=seat_ids, status=TripSeat.Status.HELD).order_by(
            "seat_id"
        )
    )
    if len(trip_seats) != len(seat_ids):
        raise trip_services.SeatsUnavailableError(set(seat_ids) - {ts.seat_id for ts in trip_seats})

    subtotal = estimate_subtotal(trip, currency, len(trip_seats))
    fee = _quantize(_booking_fee(currency))
    discount = _quantize(discount_amount)
    total = _quantize(subtotal - discount + fee)

    pnr = generate_pnr(exists_fn=_exists_pnr)
    booking = Booking.objects.create(
        pnr=pnr,
        trip=trip,
        user=user,
        contact_phone=normalize_phone(contact_phone),
        contact_email=contact_email,
        booking_channel=booking_channel,
        booked_by_staff=booked_by_staff,
        currency=currency,
        subtotal_amount=subtotal,
        discount_amount=discount,
        fee_amount=fee,
        total_amount=total,
        promo_code=promo_code,
        boarding_stop_id=boarding_stop_id,
        drop_stop_id=drop_stop_id,
    )
    BookingPassenger.objects.bulk_create(
        [
            BookingPassenger(
                booking=booking,
                trip_seat=trip_seat,
                full_name=passenger["full_name"],
                age=passenger.get("age"),
                gender=passenger.get("gender", ""),
                phone=passenger.get("phone", ""),
                id_document_number=passenger.get("id_document_number", ""),
            )
            for trip_seat, passenger in zip(trip_seats, passengers, strict=True)
        ]
    )
    return booking


def confirm_booking(booking: Booking) -> Booking:
    """Called after payment succeeds. Never call this across the gateway request itself."""
    if booking.status != Booking.Status.PENDING_PAYMENT:
        raise BookingStateError(
            _("Booking %(pnr)s is %(status)s, not pending_payment.")
            % {"pnr": booking.pnr, "status": booking.status}
        )

    seat_ids = list(booking.passengers.values_list("trip_seat__seat_id", flat=True))
    trip_services.confirm_seats(booking.trip_id, seat_ids)

    booking.status = Booking.Status.CONFIRMED
    booking.qr_token = sign_qr_token({"pnr": booking.pnr, "booking_id": booking.id})
    booking.save(update_fields=["status", "qr_token", "updated_at"])

    from apps.notifications import services as notification_services

    notification_services.notify_booking_confirmed(booking)
    return booking


def estimate_cancellation_refund(booking: Booking) -> tuple[Decimal, Decimal]:
    """Public so the frontend can show the refund amount before the user
    confirms cancelling — never a client-side guess at the tier policy.
    Mirrors cancel_booking's own math exactly, without mutating anything."""
    if booking.status != Booking.Status.CONFIRMED:
        return Decimal(0), Decimal("0.00")

    hours_before = Decimal((booking.trip.departure_at - timezone.now()).total_seconds()) / Decimal(3600)
    refund_percent = _refund_percent_for(hours_before)
    refund_amount = _quantize(booking.total_amount * refund_percent / 100)
    return refund_percent, refund_amount


def estimate_reschedule_charge(booking: Booking, new_trip: Trip) -> tuple[Decimal, Decimal]:
    """Public so the frontend can show the fare difference before the user
    confirms rescheduling. Mirrors reschedule_booking's math exactly,
    without touching seats or saving anything."""
    num_seats = booking.passengers.count()
    new_subtotal = estimate_subtotal(new_trip, booking.currency, num_seats)
    fare_difference = _quantize(new_subtotal - booking.subtotal_amount)
    reschedule_fee = _quantize(_reschedule_fee(booking.currency))
    return fare_difference, reschedule_fee


def cancel_booking(booking: Booking, *, reason: str = "", actor=None) -> Booking:
    if booking.status not in (Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED):
        raise BookingStateError(
            _("Booking %(pnr)s is %(status)s and cannot be cancelled.")
            % {"pnr": booking.pnr, "status": booking.status}
        )

    before_status = booking.status
    seat_ids = list(booking.passengers.values_list("trip_seat__seat_id", flat=True))
    was_confirmed = booking.status == Booking.Status.CONFIRMED
    # Computed before the status flip below — estimate_cancellation_refund
    # only returns a nonzero refund for a still-CONFIRMED booking.
    refund_percent, refund_amount = estimate_cancellation_refund(booking)

    with transaction.atomic():
        if was_confirmed:
            trip_services.free_booked_seats(booking.trip_id, seat_ids)
        else:
            trip_services.release_seats(booking.trip_id, seat_ids)

        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = reason
        booking.refund_percentage = refund_percent
        booking.refund_amount = refund_amount
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancellation_reason",
                "refund_percentage",
                "refund_amount",
                "updated_at",
            ]
        )
    log_action(
        actor=actor,
        action=AuditLog.Action.BOOKING_CANCELLED,
        target=booking,
        before={"status": before_status},
        after={"status": booking.status, "reason": reason, "refund_amount": str(refund_amount)},
    )
    return booking


def reschedule_booking(booking: Booking, new_trip_id, new_hold_token: str) -> Booking:
    """
    Moves a CONFIRMED booking to another trip. The new seats must already be
    held (via the normal hold-seats endpoint against new_trip_id) — this
    mirrors create_booking's hold-token pattern rather than letting the
    server silently auto-pick seats. Passenger records carry over unchanged,
    just re-linked to the new TripSeats.
    """
    if booking.status != Booking.Status.CONFIRMED:
        raise BookingStateError(
            _("Booking %(pnr)s is %(status)s and cannot be rescheduled.")
            % {"pnr": booking.pnr, "status": booking.status}
        )

    payload = trip_services.verify_hold_token(new_hold_token)
    if payload["trip_id"] != new_trip_id:
        raise trip_services.InvalidHoldTokenError(_("This hold token is for a different trip."))

    old_passengers = list(booking.passengers.select_related("trip_seat").order_by("id"))
    new_seat_ids = payload["seat_ids"]
    if len(new_seat_ids) != len(old_passengers):
        raise InvalidPassengerCountError(
            _("%(seats)s held seats for %(passengers)s existing passengers.")
            % {"seats": len(new_seat_ids), "passengers": len(old_passengers)}
        )

    with transaction.atomic():
        new_trip = Trip.objects.get(id=new_trip_id)
        old_trip_id = booking.trip_id
        old_seat_ids = [p.trip_seat.seat_id for p in old_passengers]

        new_trip_seats = trip_services.confirm_seats(new_trip_id, new_seat_ids)
        trip_services.free_booked_seats(old_trip_id, old_seat_ids)

        for passenger, new_trip_seat in zip(old_passengers, new_trip_seats, strict=True):
            passenger.trip_seat = new_trip_seat
            passenger.save(update_fields=["trip_seat", "updated_at"])

        fare_difference, reschedule_fee = estimate_reschedule_charge(booking, new_trip)
        new_subtotal = booking.subtotal_amount + fare_difference

        booking.previous_trip_id = old_trip_id
        booking.trip = new_trip
        booking.subtotal_amount = new_subtotal
        booking.fare_difference_amount = fare_difference
        booking.reschedule_fee_amount = reschedule_fee
        booking.total_amount = _quantize(
            booking.total_amount + fare_difference + reschedule_fee
        )
        booking.rescheduled_at = timezone.now()
        booking.save(
            update_fields=[
                "previous_trip",
                "trip",
                "subtotal_amount",
                "fare_difference_amount",
                "reschedule_fee_amount",
                "total_amount",
                "rescheduled_at",
                "updated_at",
            ]
        )
    return booking


def find_returning_customer(operator, contact_phone: str) -> dict | None:
    """
    Counter-booking autofill (S1): the passengers and email from this
    customer's most recent booking *with this operator*. Deliberately scoped
    to the operator's own bookings — a counter agent must not be able to
    look up anyone's travel history across the whole marketplace.
    """
    bookings = Booking.objects.filter(
        trip__route__operator=operator, contact_phone=normalize_phone(contact_phone)
    )
    latest = bookings.order_by("-created_at").prefetch_related("passengers").first()
    if latest is None:
        return None
    return {
        "contact_phone": latest.contact_phone,
        "contact_email": latest.contact_email,
        "bookings_count": bookings.count(),
        "passengers": [
            {"full_name": p.full_name, "age": p.age, "gender": p.gender, "phone": p.phone}
            for p in latest.passengers.order_by("id")
        ],
    }


def find_guest_booking(pnr: str, phone: str) -> Booking | None:
    """Looks up a booking by PNR + the exact contact phone on file — no
    account needed. `phone` must already be normalized (apps.accounts.services
    .normalize_phone). Shared by the guest-lookup API (GuestBookingLookupView)
    and the server-rendered guest-lookup page."""
    return Booking.objects.filter(pnr=pnr.upper(), contact_phone=phone).first()
