"""
Conductor-facing boarding: validate a scanned QR against a specific trip and
record every passenger on that booking as boarded, in one atomic step.
Business logic only — views just translate HTTP <-> these calls, per
CLAUDE.md's hard rules.
"""

import hashlib

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.bookings.models import Booking, BookingPassenger
from apps.core.utils import verify_qr_token

from .models import BoardingRecord


class BoardingError(Exception):
    code = "boarding_error"


class InvalidQrTokenError(BoardingError):
    code = "qr_invalid"


class BookingNotConfirmedError(BoardingError):
    code = "booking_not_confirmed"


class WrongTripError(BoardingError):
    code = "wrong_trip"


class AlreadyBoardedError(BoardingError):
    code = "already_boarded"

    def __init__(self, message, *, first_scanned_at=None):
        super().__init__(message)
        self.first_scanned_at = first_scanned_at


class BookingNotFoundError(BoardingError):
    code = "booking_not_found"


def qr_token_fingerprint(token: str) -> str:
    """
    SHA-256 of a booking's signed QR token, shipped in the conductor
    manifest. Offline, the conductor app hashes the scanned token and
    compares — so a forged QR with a real PNR still fails validation, and
    no signing secret ever has to live on the device.
    """
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def _board_booking(booking: Booking, trip_id, *, scanned_by=None, scanned_at=None) -> list[BoardingRecord]:
    """Boards every passenger on an already-looked-up booking. Shared by the
    QR scan and the manual PNR fallback so both apply the same rules."""
    if booking.status != Booking.Status.CONFIRMED:
        raise BookingNotConfirmedError(f"Booking {booking.pnr} is {booking.status}, not confirmed.")

    if booking.trip_id != trip_id:
        raise WrongTripError(f"Booking {booking.pnr} is for a different trip.")

    first = (
        BoardingRecord.objects.filter(booking=booking, status=BoardingRecord.Status.BOARDED)
        .order_by("scanned_at")
        .first()
    )
    if first is not None:
        raise AlreadyBoardedError(
            f"Booking {booking.pnr} has already boarded.", first_scanned_at=first.scanned_at
        )

    now = scanned_at or timezone.now()
    passengers = list(booking.passengers.all())
    # A passenger the operator marked no-show (mark_passengers_boarding) who
    # then turns up late already has a record — flip it rather than insert a
    # second one, which the unique constraint would reject as "already
    # boarded".
    existing = {
        record.passenger_id: record
        for record in BoardingRecord.objects.select_for_update().filter(passenger__in=passengers)
    }
    for record in existing.values():
        record.status = BoardingRecord.Status.BOARDED
        record.scanned_by = scanned_by
        record.scanned_at = now
    BoardingRecord.objects.bulk_update(
        existing.values(), ["status", "scanned_by", "scanned_at", "updated_at"]
    )
    try:
        created = BoardingRecord.objects.bulk_create(
            [
                BoardingRecord(
                    booking=booking,
                    passenger=passenger,
                    scanned_by=scanned_by,
                    scanned_at=now,
                    status=BoardingRecord.Status.BOARDED,
                )
                for passenger in passengers
                if passenger.id not in existing
            ]
        )
    except IntegrityError as exc:
        # A concurrent scan won the unique-constraint race between our
        # existence check above and this insert — the whole function is
        # atomic, so this aborts cleanly with nothing partially written.
        raise AlreadyBoardedError(f"Booking {booking.pnr} has already boarded.") from exc
    return [*existing.values(), *created]


@transaction.atomic
def validate_qr_and_board(token: str, trip_id, *, scanned_by=None) -> list[BoardingRecord]:
    """
    Verifies the signed QR token (raises on tampering/expiry), confirms the
    booking is CONFIRMED and for this exact trip, then boards every passenger
    on it. Rejects a re-scan of an already-boarded booking with a distinct
    error code rather than silently no-op'ing, per the playbook.
    """
    try:
        payload = verify_qr_token(token)
    except (signing.BadSignature, signing.SignatureExpired) as exc:
        raise InvalidQrTokenError("This QR code is invalid or has expired.") from exc

    booking = (
        Booking.objects.select_related("trip")
        .filter(id=payload.get("booking_id"), pnr=payload.get("pnr"))
        .first()
    )
    if booking is None:
        raise InvalidQrTokenError("This QR code does not match any booking.")
    return _board_booking(booking, trip_id, scanned_by=scanned_by)


@transaction.atomic
def board_by_pnr(pnr: str, trip, *, scanned_by=None) -> list[BoardingRecord]:
    """
    Manual fallback when the camera or the passenger's screen fails: the
    conductor types the PNR. Looked up within the trip's own operator only,
    so a conductor can't probe other operators' bookings by guessing PNRs.
    """
    booking = (
        Booking.objects.select_related("trip")
        .filter(pnr=pnr.strip().upper(), trip__route__operator_id=trip.route.operator_id)
        .first()
    )
    if booking is None:
        raise BookingNotFoundError(f"No booking {pnr.strip().upper()} for this operator.")
    return _board_booking(booking, trip.id, scanned_by=scanned_by)


def sync_offline_scans(trip, scans: list[dict], *, scanned_by=None) -> list[dict]:
    """
    Applies scans the conductor app queued while offline (SRS 3.4). Each
    scan is {"client_scan_id", "passenger_id", "status", "scanned_at"}; the
    result for each is one of:
      applied            — recorded (new row, or a no-show flipped to boarded)
      duplicate          — already recorded the same way (a retried sync, or a
                           second device) — nothing written, never a 2nd row
      superseded         — ignored: an offline "no-show" for someone who has
                           since boarded (boarded always wins)
      rejected:<reason>  — not a confirmed passenger of this trip
    Idempotent by construction: BoardingRecord is unique per passenger, and
    each scan is its own small transaction, so a retry after a dropped
    response can never double-board anyone.
    """
    now = timezone.now()
    passengers = {
        p.public_id: p
        for p in BookingPassenger.objects.select_related("booking").filter(
            public_id__in=[scan["passenger_id"] for scan in scans], booking__trip=trip
        )
    }
    results = []
    for scan in scans:
        result = {"client_scan_id": scan["client_scan_id"], "passenger_id": scan["passenger_id"]}
        passenger = passengers.get(scan["passenger_id"])
        if passenger is None:
            results.append({**result, "result": "rejected", "reason": "not_on_trip"})
            continue
        if passenger.booking.status != Booking.Status.CONFIRMED:
            results.append({**result, "result": "rejected", "reason": "booking_not_confirmed"})
            continue
        scanned_at = min(scan.get("scanned_at") or now, now)
        with transaction.atomic():
            record = BoardingRecord.objects.select_for_update().filter(passenger=passenger).first()
            if record is None:
                try:
                    with transaction.atomic():
                        BoardingRecord.objects.create(
                            booking=passenger.booking,
                            passenger=passenger,
                            scanned_by=scanned_by,
                            scanned_at=scanned_at,
                            status=scan["status"],
                        )
                    outcome = "applied"
                except IntegrityError:
                    outcome = "duplicate"  # another device synced this passenger a moment ago
            elif record.status == scan["status"]:
                outcome = "duplicate"
            elif record.status == BoardingRecord.Status.BOARDED:
                outcome = "superseded"
            else:  # no_show -> boarded
                record.status = BoardingRecord.Status.BOARDED
                record.scanned_by = scanned_by
                record.scanned_at = scanned_at
                record.save(update_fields=["status", "scanned_by", "scanned_at", "updated_at"])
                outcome = "applied"
        results.append({**result, "result": outcome})
    return results


@transaction.atomic
def mark_passengers_boarding(trip, passenger_public_ids, *, status: str, marked_by=None) -> int:
    """
    Operator manifest bulk action: record the given passengers as boarded or
    no-show. Only passengers on CONFIRMED bookings for this trip are
    touched (anything else in the list is ignored, never an error — the
    manifest may be a few seconds stale). Returns how many were recorded.
    """
    passengers = list(
        BookingPassenger.objects.select_related("booking").filter(
            public_id__in=passenger_public_ids,
            booking__trip=trip,
            booking__status=Booking.Status.CONFIRMED,
        )
    )
    now = timezone.now()
    for passenger in passengers:
        BoardingRecord.objects.update_or_create(
            passenger=passenger,
            defaults={
                "booking": passenger.booking,
                "status": status,
                "scanned_by": marked_by,
                "scanned_at": now,
            },
        )
    return len(passengers)


def _payment_status(booking, pending_counter_booking_ids) -> str:
    if booking.status == Booking.Status.CONFIRMED:
        return "paid"
    if booking.id in pending_counter_booking_ids:
        return "pay_at_counter"
    return "pending"


def get_manifest(trip, *, include_pending: bool = False):
    """
    The passenger list for `trip`, one row per passenger, shaped flat so a
    conductor's app can download it once and cache it for offline
    validation (compare a scanned PNR against this list without a server
    round-trip, then sync the actual boarding scans back when connectivity
    returns).

    Conductors get CONFIRMED bookings only (only those can board). The
    operator's manifest screen passes include_pending=True to also list
    pending_payment bookings — mostly pay-at-counter customers who still
    need to pay before boarding.
    """
    from apps.payments.models import Payment

    statuses = [Booking.Status.CONFIRMED]
    if include_pending:
        statuses.append(Booking.Status.PENDING_PAYMENT)

    records = {
        passenger_id: (status, scanned_at)
        for passenger_id, status, scanned_at in BoardingRecord.objects.filter(booking__trip=trip).values_list(
            "passenger_id", "status", "scanned_at"
        )
    }
    pending_counter_booking_ids = set(
        Payment.objects.filter(
            booking__trip=trip, provider=Payment.Provider.COUNTER, status=Payment.Status.PENDING
        ).values_list("booking_id", flat=True)
    )
    # trip_seat is a plain FK (not OneToOne — a cancelled booking's row
    # never gets deleted, see BookingPassenger.trip_seat's docstring), so a
    # TripSeat can have multiple historical BookingPassenger rows; at most
    # one has a non-terminal booking status at any given time, which is the
    # one this filter/prefetch picks out. `.distinct()` is cheap insurance
    # against that invariant, not a correctness requirement on its own.
    trip_seats = (
        trip.trip_seats.filter(
            booking_passenger__isnull=False,
            booking_passenger__booking__status__in=statuses,
        )
        .select_related("seat")
        .prefetch_related(
            Prefetch(
                "booking_passenger",
                queryset=BookingPassenger.objects.filter(booking__status__in=statuses).select_related(
                    "booking__boarding_stop__stop", "booking__drop_stop__stop"
                ),
            )
        )
        .order_by("seat__deck", "seat__row_position", "seat__col_position")
        .distinct()
    )
    rows = []
    for ts in trip_seats:
        passenger = ts.booking_passenger.all()[0]
        booking = passenger.booking
        boarding_stop = booking.boarding_stop.stop if booking.boarding_stop_id else None
        boarding_status, scanned_at = records.get(passenger.id, ("", None))
        rows.append(
            {
                "booking_public_id": booking.public_id,
                "pnr": booking.pnr,
                "passenger_public_id": passenger.public_id,
                "full_name": passenger.full_name,
                "age": passenger.age,
                "gender": passenger.gender,
                "phone": passenger.phone or booking.contact_phone,
                "seat_number": ts.seat.seat_number,
                "seat_public_id": ts.seat.public_id,
                "boarding_stop_name_en": boarding_stop.name_en if boarding_stop else "",
                "boarding_stop_name_km": boarding_stop.name_km if boarding_stop else "",
                "booking_status": booking.status,
                "booking_channel": booking.booking_channel,
                "payment_status": _payment_status(booking, pending_counter_booking_ids),
                "boarding_status": boarding_status or "",
                "boarded": boarding_status == BoardingRecord.Status.BOARDED,
                "scanned_at": scanned_at,
                "qr_token_sha256": qr_token_fingerprint(booking.qr_token),
            }
        )
    return rows
