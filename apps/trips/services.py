"""
Trip and seat-hold business logic. See docs/SRS.md §6.3 for the hold/release
protocol this module implements — read that before touching hold_seats.
"""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Trip, TripSeat

_HOLD_TOKEN_SALT = "apps.trips.services.hold_token"


class TripLockedError(Exception):
    """Another hold/release/confirm is in progress for this trip right now."""

    code = "trip_locked"


class SeatsUnavailableError(Exception):
    """One or more requested seats are missing or not in the expected state."""

    code = "seats_unavailable"

    def __init__(self, seat_ids):
        self.seat_ids = list(seat_ids)
        super().__init__(f"Seats not available: {self.seat_ids}")


class InvalidHoldTokenError(Exception):
    code = "hold_token_invalid"


class TripNotBookableError(Exception):
    """The trip is cancelled/departed/completed, or already left."""

    code = "trip_not_bookable"


class TripSeatLayoutDesyncedError(Exception):
    """The trip's TripSeat rows were snapshotted from a seat layout the bus
    no longer uses (see apps/fleet/services.py's BusInUseError docstring for
    how this happens) — new holds can never resolve against Seat rows from
    the bus's current layout, so this must be caught before that lookup
    produces a confusing "Unknown seats" 400."""

    code = "seat_layout_desynced"


class TripScheduleError(Exception):
    code = "trip_schedule_error"


class BusConflictError(TripScheduleError):
    """The bus is already scheduled on an overlapping, non-cancelled trip."""

    code = "bus_conflict"

    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        when = ", ".join(
            timezone.localtime(t.departure_at).strftime("%Y-%m-%d %H:%M") for t in self.conflicts[:5]
        )
        super().__init__(f"This bus is already scheduled at an overlapping time: {when}.")


class TripStatusError(Exception):
    code = "trip_status_error"


# A delayed trip still runs, so it stays bookable (holds never checked trip
# status at all before Step 15 — see ensure_trip_bookable).
BOOKABLE_STATUSES = (Trip.Status.SCHEDULED, Trip.Status.DELAYED)
_TERMINAL_STATUSES = (Trip.Status.CANCELLED, Trip.Status.DEPARTED, Trip.Status.COMPLETED)
MAX_RECURRING_TRIPS = 120


def _lock_key(trip_id) -> str:
    return f"trip-lock:{trip_id}"


@contextmanager
def _trip_lock(trip_id):
    """
    Fails fast (no blocking/retry) if another hold/release/confirm is
    already running for this trip — see SRS 6.3 step 1. The real
    correctness guarantee is the DB's SELECT ... FOR UPDATE below; this
    lock exists only to keep DB transactions short under contention on a
    popular trip, instead of piling up threads on a DB row lock queue.
    """
    key = _lock_key(trip_id)
    if not cache.add(key, "1", timeout=settings.SEAT_LOCK_TTL_SECONDS):
        raise TripLockedError(_("Trip %(trip_id)s is busy — try again.") % {"trip_id": trip_id})
    try:
        yield
    finally:
        cache.delete(key)


_SEARCH_CACHE_VERSION_KEY = "trip-search:version"


def search_cache_version() -> int:
    version = cache.get(_SEARCH_CACHE_VERSION_KEY)
    if version is None:
        cache.set(_SEARCH_CACHE_VERSION_KEY, 1, timeout=None)
        return 1
    return version


def bump_search_cache_version() -> None:
    """
    Invalidates every cached trip-search result page in one write, instead
    of enumerating the unbounded set of origin/destination/date/bus_type/
    operator key combinations a single seat hold, trip creation, or status
    change could have made stale. Called via transaction.on_commit so a
    rolled-back mutation never bumps it and a stale cached page never
    outlives the change that invalidated it.
    """
    try:
        cache.incr(_SEARCH_CACHE_VERSION_KEY)
    except ValueError:
        cache.set(_SEARCH_CACHE_VERSION_KEY, 1, timeout=None)


def with_operator_stats(qs):
    """
    Annotates seats_total/seats_sold/bookings_count/revenue_usd/revenue_khr
    per trip. Each is a correlated subquery rather than a Count/Sum over
    joins: joining trip_seats and bookings in one annotate() fans out rows
    and multiplies the revenue sum by the seat count. Revenue is kept per
    currency — a KHR booking's total must never be added to a USD one.
    """
    from django.db.models import Count, DecimalField, IntegerField, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    from apps.bookings.models import Booking

    def _count(inner_qs):
        return Coalesce(
            Subquery(
                inner_qs.order_by().values("trip_id").annotate(n=Count("id")).values("n")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        )

    def _revenue(currency):
        inner = (
            Booking.objects.filter(
                trip_id=OuterRef("pk"),
                status__in=(Booking.Status.CONFIRMED, Booking.Status.COMPLETED),
                currency=currency,
            )
            .order_by()
            .values("trip_id")
            .annotate(total=Sum("total_amount"))
            .values("total")[:1]
        )
        return Coalesce(
            Subquery(inner, output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

    active =(Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED, Booking.Status.COMPLETED)
    return qs.annotate(
        seats_total=_count(TripSeat.objects.filter(trip_id=OuterRef("pk"))),
        seats_sold=_count(TripSeat.objects.filter(trip_id=OuterRef("pk"), status=TripSeat.Status.BOOKED)),
        bookings_count=_count(Booking.objects.filter(trip_id=OuterRef("pk"), status__in=active)),
        revenue_usd=_revenue(Booking.Currency.USD),
        revenue_khr=_revenue(Booking.Currency.KHR),
    )


def ensure_trip_bookable(trip: Trip) -> None:
    """Seats may only be held on a trip that will actually run and hasn't
    left yet. Before Step 15 the hold endpoint accepted any trip, so a
    cancelled trip's seats could still be held and booked through the API."""
    if trip.status not in BOOKABLE_STATUSES:
        raise TripNotBookableError(
            _("This trip is %(status)s and can no longer be booked.") % {"status": trip.status}
        )
    if trip.departure_at <= timezone.now():
        raise TripNotBookableError(_("This trip has already departed."))
    if trip.trip_seats.exclude(seat__seat_layout_id=trip.bus.seat_layout_id).exists():
        raise TripSeatLayoutDesyncedError(
            _("This trip's seat map is out of date. Please contact support to book it.")
        )


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def search_trips(*, origin=None, destination=None, date=None, bus_type=None, operator=None):
    """Bookable, not-yet-departed trips matching the given filters. `origin`/
    `destination` are City `public_id`s, `operator` is an Operator `public_id`,
    `date` is an already-parsed `date` matched via a local-day range rather
    than `departure_at__date` (with USE_TZ, `__date` compiles to
    CONVERT_TZ(), which is NULL on a MySQL/MariaDB server without its time
    zone tables loaded — this dev WAMP MariaDB). Shared by the public search
    API (TripSearchView) and the server-rendered search page."""
    qs = Trip.objects.select_related(
        "route",
        "route__operator",
        "route__origin_city",
        "route__destination_city",
        "bus",
        "bus__seat_layout",
    ).filter(status__in=BOOKABLE_STATUSES, departure_at__gt=timezone.now())

    if origin:
        qs = qs.filter(route__origin_city__public_id=origin)
    if destination:
        qs = qs.filter(route__destination_city__public_id=destination)
    if date:
        start, end = local_day_bounds(date)
        qs = qs.filter(departure_at__gte=start, departure_at__lt=end)
    if bus_type:
        qs = qs.filter(bus__seat_layout__bus_type=bus_type)
    if operator:
        qs = qs.filter(route__operator__public_id=operator)

    return qs


def find_bus_conflicts(bus_id, departure_at, arrival_at, *, exclude_trip_id=None):
    """Non-cancelled trips on the same bus whose [departure, arrival) window
    overlaps the given one."""
    qs = Trip.objects.filter(bus_id=bus_id, departure_at__lt=arrival_at, arrival_at__gt=departure_at).exclude(
        status=Trip.Status.CANCELLED
    )
    if exclude_trip_id is not None:
        qs = qs.exclude(id=exclude_trip_id)
    return qs.order_by("departure_at")


def _validate_schedule(bus, departure_at, arrival_at, *, exclude_trip_id=None) -> None:
    from apps.fleet.models import Bus

    if arrival_at <= departure_at:
        raise TripScheduleError(_("Arrival must be after departure."))
    if bus.status != Bus.Status.ACTIVE:
        raise TripScheduleError(
            _("Bus %(plate_number)s is %(status)s and cannot be scheduled.")
            % {"plate_number": bus.plate_number, "status": bus.status}
        )
    conflicts = list(find_bus_conflicts(bus.id, departure_at, arrival_at, exclude_trip_id=exclude_trip_id))
    if conflicts:
        raise BusConflictError(conflicts)


@transaction.atomic
def create_trip(**fields) -> Trip:
    """Single trip creation: schedule checks, then one TripSeat per seat."""
    _validate_schedule(fields["bus"], fields["departure_at"], fields["arrival_at"])
    trip = Trip.objects.create(**fields)
    generate_trip_seats(trip)
    transaction.on_commit(bump_search_cache_version)
    return trip


def update_trip(trip: Trip, **changes) -> Trip:
    """
    Edits a not-yet-finished trip. Changing the bus regenerates the trip's
    seat inventory from the new bus's layout, which is only safe while no
    seat is held or booked — otherwise BookingPassengers would point at
    TripSeats from a layout the trip no longer uses.
    """
    if trip.status in _TERMINAL_STATUSES:
        raise TripScheduleError(_("A %(status)s trip cannot be edited.") % {"status": trip.status})

    new_bus = changes.get("bus", trip.bus)
    bus_changed = new_bus.id != trip.bus_id
    departure_at = changes.get("departure_at", trip.departure_at)
    arrival_at = changes.get("arrival_at", trip.arrival_at)
    if bus_changed or "departure_at" in changes or "arrival_at" in changes:
        _validate_schedule(new_bus, departure_at, arrival_at, exclude_trip_id=trip.id)

    if not bus_changed:
        for field, value in changes.items():
            setattr(trip, field, value)
        trip.save()
        transaction.on_commit(bump_search_cache_version)
        return trip

    with _trip_lock(trip.id):
        with transaction.atomic():
            seats = list(TripSeat.objects.select_for_update().filter(trip=trip).order_by("seat_id"))
            if any(ts.status != TripSeat.Status.AVAILABLE for ts in seats):
                raise TripScheduleError(
                    "The bus can't be changed once seats are held or booked on this trip."
                )
            TripSeat.objects.filter(trip=trip).delete()
            for field, value in changes.items():
                setattr(trip, field, value)
            new_seats = list(new_bus.seat_layout.seats.all())
            TripSeat.objects.bulk_create(
                [TripSeat(trip=trip, seat=seat, status=TripSeat.Status.AVAILABLE) for seat in new_seats]
            )
            trip.seats_available = len(new_seats)
            trip.save()
    transaction.on_commit(bump_search_cache_version)
    return trip


def plan_recurring_departures(
    *, start_date: date, end_date: date, weekdays: list[int], departure_time: time, duration_minutes: int
) -> list[tuple[datetime, datetime]]:
    """
    (departure_at, arrival_at) pairs for every matching local date in
    [start_date, end_date]. `weekdays` uses Python's Monday=0..Sunday=6;
    empty means every day. Times are interpreted in the project time zone
    (Asia/Phnom_Penh) and stored as aware datetimes (UTC in the DB).
    """
    if end_date < start_date:
        raise TripScheduleError(_("End date must not be before start date."))
    if duration_minutes <= 0:
        raise TripScheduleError(_("Duration must be positive."))
    tz = timezone.get_current_timezone()
    allowed = set(weekdays) if weekdays else set(range(7))
    departures = []
    day = start_date
    while day <= end_date:
        if day.weekday() in allowed:
            departure_at = timezone.make_aware(datetime.combine(day, departure_time), tz)
            departures.append((departure_at, departure_at + timedelta(minutes=duration_minutes)))
        day += timedelta(days=1)
    if len(departures) > MAX_RECURRING_TRIPS:
        raise TripScheduleError(
            _("At most %(max)s trips can be created at once.") % {"max": MAX_RECURRING_TRIPS}
        )
    return departures


def peak_fares(base_usd: Decimal, base_khr: Decimal, multiplier: Decimal) -> tuple[Decimal, Decimal]:
    usd = (base_usd * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    khr = (base_khr * multiplier).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return usd, khr


@transaction.atomic
def create_recurring_trips(
    *,
    route,
    bus,
    start_date: date,
    end_date: date,
    weekdays: list[int],
    departure_time: time,
    duration_minutes: int,
    base_fare_usd: Decimal,
    base_fare_khr: Decimal,
    peak_start_date: date | None = None,
    peak_end_date: date | None = None,
    peak_multiplier: Decimal | None = None,
    **crew,
) -> list[Trip]:
    """
    All-or-nothing: every departure is checked for a bus conflict before any
    trip is written, so a half-created schedule never needs cleaning up.
    Peak pricing (optional) multiplies both fares for departures whose local
    date falls in [peak_start_date, peak_end_date].
    """
    departures = plan_recurring_departures(
        start_date=start_date,
        end_date=end_date,
        weekdays=weekdays,
        departure_time=departure_time,
        duration_minutes=duration_minutes,
    )
    if not departures:
        raise TripScheduleError(_("No dates in that range match the chosen weekdays."))

    _validate_schedule(bus, departures[0][0], departures[0][1])
    conflicts = []
    for departure_at, arrival_at in departures[1:]:
        conflicts.extend(find_bus_conflicts(bus.id, departure_at, arrival_at))
    if conflicts:
        raise BusConflictError(conflicts)

    in_peak = bool(peak_multiplier and peak_start_date and peak_end_date)
    trips = []
    for departure_at, arrival_at in departures:
        fare_usd, fare_khr = base_fare_usd, base_fare_khr
        if in_peak and peak_start_date <= timezone.localtime(departure_at).date() <= peak_end_date:
            fare_usd, fare_khr = peak_fares(base_fare_usd, base_fare_khr, peak_multiplier)
        trip = Trip.objects.create(
            route=route,
            bus=bus,
            departure_at=departure_at,
            arrival_at=arrival_at,
            base_fare_usd=fare_usd,
            base_fare_khr=fare_khr,
            **crew,
        )
        generate_trip_seats(trip)
        trips.append(trip)
    transaction.on_commit(bump_search_cache_version)
    return trips


def generate_trip_seats(trip: Trip) -> list[TripSeat]:
    """Creates one TripSeat per Seat on the trip's bus, all available."""
    seats = list(trip.bus.seat_layout.seats.all())
    trip_seats = [TripSeat(trip=trip, seat=seat, status=TripSeat.Status.AVAILABLE) for seat in seats]
    with transaction.atomic():
        TripSeat.objects.bulk_create(trip_seats)
        trip.seats_available = len(seats)
        trip.save(update_fields=["seats_available", "updated_at"])
    return trip_seats


def _sign_hold_token(trip_id, seat_ids, held_until) -> str:
    payload = {"trip_id": trip_id, "seat_ids": sorted(seat_ids), "held_until": held_until.isoformat()}
    return signing.dumps(payload, salt=_HOLD_TOKEN_SALT)


def verify_hold_token(token: str) -> dict:
    try:
        payload = signing.loads(token, salt=_HOLD_TOKEN_SALT)
    except signing.BadSignature as exc:
        raise InvalidHoldTokenError(
            _("This hold token is invalid or has been tampered with.")
        ) from exc

    held_until = timezone.datetime.fromisoformat(payload["held_until"])
    if held_until < timezone.now():
        raise InvalidHoldTokenError(_("This seat hold has expired."))
    return payload


def hold_seats(trip_id, seat_ids, *, user=None, session_key=None) -> str:
    """
    The critical path (SRS 6.3): locks the trip, takes row locks on the
    target TripSeats in a fixed order, verifies every seat is available,
    holds them, and returns a signed token proving the hold. Raises
    TripLockedError or SeatsUnavailableError on failure — never partially
    holds a subset of the requested seats.
    """
    seat_ids = list(dict.fromkeys(seat_ids))  # de-dupe, preserve order for the caller
    if not seat_ids:
        raise ValueError("seat_ids must not be empty.")
    if user is None and not session_key:
        raise ValueError("hold_seats requires a user or a session_key.")

    held_until = timezone.now() + timedelta(minutes=settings.SEAT_HOLD_MINUTES)

    with _trip_lock(trip_id):
        with transaction.atomic():
            trip_seats = list(
                TripSeat.objects.select_for_update()
                .filter(trip_id=trip_id, seat_id__in=seat_ids)
                .order_by("seat_id")
            )
            found_seat_ids = {ts.seat_id for ts in trip_seats}
            missing = set(seat_ids) - found_seat_ids
            if missing:
                raise SeatsUnavailableError(missing)

            unavailable = [ts.seat_id for ts in trip_seats if ts.status != TripSeat.Status.AVAILABLE]
            if unavailable:
                raise SeatsUnavailableError(unavailable)

            for ts in trip_seats:
                ts.status = TripSeat.Status.HELD
                ts.held_until = held_until
                ts.held_by_user = user
                ts.held_by_session_key = session_key or ""
            TripSeat.objects.bulk_update(
                trip_seats, ["status", "held_until", "held_by_user", "held_by_session_key", "updated_at"]
            )
            Trip.objects.filter(id=trip_id).update(seats_available=F("seats_available") - len(trip_seats))

    transaction.on_commit(bump_search_cache_version)
    return _sign_hold_token(trip_id, seat_ids, held_until)


def release_seats(trip_id, seat_ids, *, user=None, session_key=None, held_before=None) -> int:
    """
    Releases held seats back to available. If a user/session_key is given,
    only seats actually held by that caller are released (the release-hold
    endpoint); with neither, every matching held seat is released
    regardless of owner (the expiry sweep — which also passes `held_before`,
    re-checked here *inside* the lock, so a hold renewed between the sweep's
    scan and this call can't be released out from under its new owner).
    Returns the count released.
    """
    with _trip_lock(trip_id):
        with transaction.atomic():
            qs = (
                TripSeat.objects.select_for_update()
                .filter(trip_id=trip_id, seat_id__in=seat_ids, status=TripSeat.Status.HELD)
                .order_by("seat_id")
            )
            if user is not None:
                qs = qs.filter(held_by_user=user)
            elif session_key:
                qs = qs.filter(held_by_session_key=session_key)
            if held_before is not None:
                qs = qs.filter(held_until__lt=held_before)
            trip_seats = list(qs)
            if not trip_seats:
                return 0

            for ts in trip_seats:
                ts.status = TripSeat.Status.AVAILABLE
                ts.held_until = None
                ts.held_by_user = None
                ts.held_by_session_key = ""
            TripSeat.objects.bulk_update(
                trip_seats, ["status", "held_until", "held_by_user", "held_by_session_key", "updated_at"]
            )
            Trip.objects.filter(id=trip_id).update(seats_available=F("seats_available") + len(trip_seats))

    transaction.on_commit(bump_search_cache_version)
    return len(trip_seats)


def confirm_seats(trip_id, seat_ids) -> list[TripSeat]:
    """
    Flips held seats to booked once payment has succeeded. Does not touch
    seats_available (already decremented at hold time). Must be called
    with a fresh hold already in hand — never call this across a
    payment-gateway request; confirm only after the gateway call returns.
    """
    with _trip_lock(trip_id):
        with transaction.atomic():
            trip_seats = list(
                TripSeat.objects.select_for_update()
                .filter(trip_id=trip_id, seat_id__in=seat_ids, status=TripSeat.Status.HELD)
                .order_by("seat_id")
            )
            if len(trip_seats) != len(set(seat_ids)):
                found = {ts.seat_id for ts in trip_seats}
                raise SeatsUnavailableError(set(seat_ids) - found)

            for ts in trip_seats:
                ts.status = TripSeat.Status.BOOKED
                ts.held_until = None
            TripSeat.objects.bulk_update(trip_seats, ["status", "held_until", "updated_at"])

    return trip_seats


def extend_hold(trip_id, seat_ids, new_held_until) -> int:
    """
    Pushes held_until further out on already-HELD seats — used for FR4.3's
    pay-at-counter deadline (COUNTER_PAYMENT_DEADLINE_MINUTES), which is
    longer than the standard SEAT_HOLD_MINUTES window a seat was first held
    under. Does not touch seats_available — still just as held as before.
    """
    with _trip_lock(trip_id):
        with transaction.atomic():
            trip_seats = list(
                TripSeat.objects.select_for_update()
                .filter(trip_id=trip_id, seat_id__in=seat_ids, status=TripSeat.Status.HELD)
                .order_by("seat_id")
            )
            if len(trip_seats) != len(set(seat_ids)):
                found = {ts.seat_id for ts in trip_seats}
                raise SeatsUnavailableError(set(seat_ids) - found)

            for ts in trip_seats:
                ts.held_until = new_held_until
            TripSeat.objects.bulk_update(trip_seats, ["held_until", "updated_at"])

    return len(trip_seats)


def free_booked_seats(trip_id, seat_ids) -> int:
    """
    Releases already-*booked* seats back to available — used by
    bookings.cancel_booking for a booking that was paid and confirmed (its
    seats are BOOKED, not HELD, so release_seats's HELD-only filter would
    silently do nothing). Unlike a hold release, this always restores
    seats_available since a booked seat was never counted in it as "held".
    """
    with _trip_lock(trip_id):
        with transaction.atomic():
            trip_seats = list(
                TripSeat.objects.select_for_update()
                .filter(trip_id=trip_id, seat_id__in=seat_ids, status=TripSeat.Status.BOOKED)
                .order_by("seat_id")
            )
            if not trip_seats:
                return 0

            for ts in trip_seats:
                ts.status = TripSeat.Status.AVAILABLE
                ts.held_until = None
            TripSeat.objects.bulk_update(trip_seats, ["status", "held_until", "updated_at"])
            Trip.objects.filter(id=trip_id).update(seats_available=F("seats_available") + len(trip_seats))

    transaction.on_commit(bump_search_cache_version)
    return len(trip_seats)


def check_status_transition(trip: Trip, status: str) -> None:
    """Cancelled/departed/completed are final; "scheduled" here means
    "back on time", so it's only valid coming out of a delay."""
    if trip.status in _TERMINAL_STATUSES:
        raise TripStatusError(_("This trip is already %(status)s.") % {"status": trip.status})
    if status == Trip.Status.SCHEDULED and trip.status != Trip.Status.DELAYED:
        raise TripStatusError(_("Only a delayed trip can be marked back on time."))


def set_trip_status(
    trip: Trip, *, status: str, delay_minutes=None, cancellation_reason: str = "", actor=None
) -> Trip:
    """
    Operator-facing status change (delayed/cancelled/back on time). Business
    logic lives here, not in the view, per CLAUDE.md's hard rules — the view
    was mutating the model directly before Step 9 wired up rider
    notifications. Back on time sends nothing (no template exists for it).
    """
    from apps.core.audit import log_action
    from apps.core.models import AuditLog

    check_status_transition(trip, status)
    if status != Trip.Status.DELAYED:
        delay_minutes = None
    before = {"status": trip.status, "delay_minutes": trip.delay_minutes}
    trip.status = status
    trip.delay_minutes = delay_minutes
    trip.cancellation_reason = cancellation_reason
    trip.save(update_fields=["status", "delay_minutes", "cancellation_reason", "updated_at"])
    transaction.on_commit(bump_search_cache_version)
    log_action(
        actor=actor,
        action=AuditLog.Action.TRIP_STATUS_CHANGED,
        target=trip,
        before=before,
        after={"status": status, "delay_minutes": delay_minutes, "reason": cancellation_reason},
    )

    from apps.notifications import services as notification_services

    if status == Trip.Status.DELAYED:
        notification_services.notify_trip_delayed(trip)
    elif status == Trip.Status.CANCELLED:
        notification_services.notify_trip_cancelled(trip)
    return trip


def release_expired_holds() -> int:
    """Sweeps every trip with an expired hold. Called by the Celery beat task."""
    now = timezone.now()
    expired_trip_ids = (
        TripSeat.objects.filter(status=TripSeat.Status.HELD, held_until__lt=now)
        .values_list("trip_id", flat=True)
        .distinct()
    )
    released = 0
    for trip_id in list(expired_trip_ids):
        seat_ids = list(
            TripSeat.objects.filter(
                trip_id=trip_id, status=TripSeat.Status.HELD, held_until__lt=now
            ).values_list("seat_id", flat=True)
        )
        if seat_ids:
            released += release_seats(trip_id, seat_ids, held_before=now)
    return released
