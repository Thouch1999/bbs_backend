import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.routes.models import City, Route, RouteStop, Stop
from apps.trips import services
from apps.trips.models import Trip, TripSeat

pytestmark = pytest.mark.django_db


def _make_trip(num_seats=10):
    operator = Operator.objects.create(name="Test Operator", contact_phone="+85511111111", status="approved")
    origin = City.objects.create(name_en="Phnom Penh", name_km="A")
    destination = City.objects.create(name_en="Siem Reap", name_km="B")
    route = Route.objects.create(operator=operator, origin_city=origin, destination_city=destination)
    layout = SeatLayout.objects.create(operator=operator, name="Layout")
    Seat.objects.bulk_create(
        [
            Seat(seat_layout=layout, seat_number=f"A{i}", deck=1, row_position=i, col_position=1)
            for i in range(1, num_seats + 1)
        ]
    )
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there) — refetch so seats[i].id is always usable.
    seats = list(Seat.objects.filter(seat_layout=layout).order_by("id"))
    bus = Bus.objects.create(
        operator=operator, seat_layout=layout, plate_number="PP-0001", amenities=["ac", "wifi"]
    )
    trip = Trip.objects.create(
        route=route,
        bus=bus,
        departure_at=timezone.now() + timedelta(days=1),
        arrival_at=timezone.now() + timedelta(days=1, hours=6),
        base_fare_usd="10.00",
        base_fare_khr="41000",
    )
    services.generate_trip_seats(trip)
    return trip, seats


def _run_in_thread(fn, *args, **kwargs):
    """Runs fn in the calling thread's own DB connection, closing it after —
    each thread needs its own connection for real cross-connection locking."""
    try:
        return fn(*args, **kwargs)
    finally:
        connection.close()


def _retrying_on_trip_locked(fn, *args, attempts=100, delay=0.05, **kwargs):
    """A real client retries a transient 'trip busy' error; these tests do the same
    so heavy thread contention on the coarse per-trip lock doesn't read as a bug."""
    last_exc = None
    for _ in range(attempts):
        try:
            return fn(*args, **kwargs)
        except services.TripLockedError as exc:
            last_exc = exc
            time.sleep(delay)
    raise last_exc


def _hold_with_retries(trip_id, seat_ids, session_key, **kwargs):
    try:
        return _retrying_on_trip_locked(
            services.hold_seats, trip_id, seat_ids, session_key=session_key, **kwargs
        )
    except services.SeatsUnavailableError as exc:
        return exc  # a definitive loss, not a retry-able condition


class TestSeatHoldConcurrency:
    def test_exactly_one_of_n_wins_a_single_seat(self, transactional_db):
        trip, seats = _make_trip(num_seats=5)
        target_seat_id = seats[0].id
        n = 20

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [
                pool.submit(
                    _run_in_thread,
                    _hold_with_retries,
                    trip.id,
                    [target_seat_id],
                    f"guest-{i}",
                )
                for i in range(n)
            ]
            results = [f.result() for f in futures]

        successes = [r for r in results if isinstance(r, str)]
        failures = [r for r in results if isinstance(r, services.SeatsUnavailableError)]

        assert len(successes) == 1
        assert len(failures) == n - 1

        trip.refresh_from_db()
        assert trip.seats_available == 4
        assert TripSeat.objects.filter(trip=trip, status=TripSeat.Status.HELD).count() == 1

    def test_multi_seat_overlapping_requests_do_not_deadlock(self, transactional_db):
        trip, seats = _make_trip(num_seats=4)
        seat_ids = [s.id for s in seats]
        # Two callers race for overlapping, oppositely-ordered seat sets —
        # the fixed seat_id ordering inside hold_seats must prevent a
        # classic "A locks 1 then wants 2, B locks 2 then wants 1" deadlock.
        pairs = [
            (seat_ids[0:2], "guest-a"),
            (list(reversed(seat_ids[0:2])), "guest-b"),
            (seat_ids[1:3], "guest-c"),
            (list(reversed(seat_ids[1:3])), "guest-d"),
        ]

        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            futures = [
                pool.submit(_run_in_thread, _hold_with_retries, trip.id, ids, session)
                for ids, session in pairs
            ]
            # If this hangs, pytest's own timeout (or the suite) will catch it;
            # the assertion below is what actually proves no deadlock occurred.
            results = [f.result(timeout=30) for f in futures]

        assert len(results) == len(pairs)

        trip.refresh_from_db()
        held_count = TripSeat.objects.filter(trip=trip, status=TripSeat.Status.HELD).count()
        assert trip.seats_available == len(seat_ids) - held_count

    def test_seats_available_never_drifts_under_mixed_load(self, transactional_db):
        trip, seats = _make_trip(num_seats=8)
        seat_ids = [s.id for s in seats]

        def hold_then_release(i):
            sid = seat_ids[i % len(seat_ids)]
            try:
                result = _hold_with_retries(trip.id, [sid], f"guest-{i}")
            except services.TripLockedError:
                return  # exhausted retries under heavy test contention; a real client would retry later
            if isinstance(result, str):
                try:
                    _retrying_on_trip_locked(
                        services.release_seats, trip.id, [sid], session_key=f"guest-{i}"
                    )
                except services.TripLockedError:
                    pass  # held seat stays held; still a consistent, non-drifting state

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(_run_in_thread, hold_then_release, i) for i in range(60)]
            for f in futures:
                f.result(timeout=30)

        trip.refresh_from_db()
        actual_available = TripSeat.objects.filter(trip=trip, status=TripSeat.Status.AVAILABLE).count()
        assert trip.seats_available == actual_available == len(seat_ids)


class TestHoldReleaseConfirm:
    def test_hold_rejects_already_held_seat(self, transactional_db):
        trip, seats = _make_trip(num_seats=3)
        services.hold_seats(trip.id, [seats[0].id], session_key="first")
        with pytest.raises(services.SeatsUnavailableError):
            services.hold_seats(trip.id, [seats[0].id], session_key="second")

    def test_hold_is_all_or_nothing_across_seats(self, transactional_db):
        trip, seats = _make_trip(num_seats=3)
        services.hold_seats(trip.id, [seats[0].id], session_key="first")

        with pytest.raises(services.SeatsUnavailableError):
            services.hold_seats(trip.id, [seats[0].id, seats[1].id], session_key="second")

        # seats[1] must NOT have been held, since the whole request failed
        seats[1].refresh_from_db()
        trip_seat = TripSeat.objects.get(trip=trip, seat=seats[1])
        assert trip_seat.status == TripSeat.Status.AVAILABLE

    def test_release_only_releases_the_holders_own_seats(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        services.hold_seats(trip.id, [seats[0].id], session_key="owner")

        released = services.release_seats(trip.id, [seats[0].id], session_key="someone-else")
        assert released == 0
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.HELD

        released = services.release_seats(trip.id, [seats[0].id], session_key="owner")
        assert released == 1
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE

    def test_confirm_flips_held_to_booked_without_touching_seats_available(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        services.hold_seats(trip.id, [seats[0].id], session_key="owner")
        trip.refresh_from_db()
        available_before = trip.seats_available

        services.confirm_seats(trip.id, [seats[0].id])

        trip.refresh_from_db()
        assert trip.seats_available == available_before
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.BOOKED

    def test_confirm_rejects_seats_not_held(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        with pytest.raises(services.SeatsUnavailableError):
            services.confirm_seats(trip.id, [seats[0].id])

    def test_free_booked_seats_restores_availability(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        services.hold_seats(trip.id, [seats[0].id], session_key="owner")
        services.confirm_seats(trip.id, [seats[0].id])
        trip.refresh_from_db()
        assert trip.seats_available == 1

        released = services.free_booked_seats(trip.id, [seats[0].id])
        assert released == 1

        trip.refresh_from_db()
        assert trip.seats_available == 2
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE

    def test_free_booked_seats_ignores_seats_not_booked(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        assert services.free_booked_seats(trip.id, [seats[0].id]) == 0

    def test_extend_hold_pushes_back_expiry_without_touching_availability(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        services.hold_seats(trip.id, [seats[0].id], session_key="owner")
        trip.refresh_from_db()
        available_before = trip.seats_available

        far_future = timezone.now() + timedelta(hours=2)
        extended = services.extend_hold(trip.id, [seats[0].id], far_future)
        assert extended == 1

        trip.refresh_from_db()
        assert trip.seats_available == available_before
        trip_seat = TripSeat.objects.get(trip=trip, seat=seats[0])
        assert trip_seat.status == TripSeat.Status.HELD
        assert trip_seat.held_until == far_future

    def test_extend_hold_rejects_seats_not_held(self, transactional_db):
        trip, seats = _make_trip(num_seats=2)
        with pytest.raises(services.SeatsUnavailableError):
            services.extend_hold(trip.id, [seats[0].id], timezone.now() + timedelta(hours=1))


class TestExpiredHoldSweep:
    def test_expired_holds_are_reclaimed(self, db):
        trip, seats = _make_trip(num_seats=3)
        services.hold_seats(trip.id, [seats[0].id, seats[1].id], session_key="someone")

        # Force the hold into the past without waiting SEAT_HOLD_MINUTES.
        TripSeat.objects.filter(trip=trip, seat__in=seats[:2]).update(
            held_until=timezone.now() - timedelta(minutes=1)
        )

        released = services.release_expired_holds()
        assert released == 2

        trip.refresh_from_db()
        assert trip.seats_available == 3
        assert TripSeat.objects.filter(trip=trip, status=TripSeat.Status.AVAILABLE).count() == 3

    def test_sweep_does_not_touch_unexpired_holds(self, db):
        trip, seats = _make_trip(num_seats=2)
        services.hold_seats(trip.id, [seats[0].id], session_key="someone")

        released = services.release_expired_holds()
        assert released == 0

        trip.refresh_from_db()
        assert trip.seats_available == 1
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.HELD

    def test_sweep_does_not_release_a_hold_renewed_after_the_scan(self, db):
        """Guards the held_before check inside release_seats (see services.py)."""
        trip, seats = _make_trip(num_seats=1)
        services.hold_seats(trip.id, [seats[0].id], session_key="first")
        past = timezone.now() - timedelta(minutes=1)
        TripSeat.objects.filter(trip=trip, seat=seats[0]).update(held_until=past)

        services.release_seats(trip.id, [seats[0].id], session_key="first")
        services.hold_seats(trip.id, [seats[0].id], session_key="second")

        released = services.release_seats(trip.id, [seats[0].id], held_before=past)
        assert released == 0
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.HELD


class TestTripSerializerExtraFields:
    def test_search_and_detail_expose_bus_type_and_amenities(self):
        trip, _seats = _make_trip(num_seats=2)
        client = APIClient()

        detail = client.get(f"/api/v1/trips/{trip.public_id}/")
        assert detail.status_code == 200
        assert detail.data["bus_type"] == "seated"
        assert detail.data["amenities"] == ["ac", "wifi"]

        search = client.get("/api/v1/trips/search/")
        assert search.status_code == 200
        assert search.data["results"][0]["bus_type"] == "seated"
        assert search.data["results"][0]["amenities"] == ["ac", "wifi"]


class TestTripSeatMapPagination:
    def test_seat_map_returns_every_seat_unpaginated(self):
        trip, seats = _make_trip(num_seats=25)
        client = APIClient()

        resp = client.get(f"/api/v1/trips/{trip.public_id}/seats/")
        assert resp.status_code == 200
        assert isinstance(resp.data, list)
        assert len(resp.data) == len(seats) == 25


class TestTripStops:
    def test_returns_stops_in_sequence_order(self):
        trip, _seats = _make_trip(num_seats=2)
        city = City.objects.create(name_en="Kampong Cham", name_km="C")
        stop_a = Stop.objects.create(city=city, name_en="Stop A", name_km="ក")
        stop_b = Stop.objects.create(city=city, name_en="Stop B", name_km="ខ")
        RouteStop.objects.create(route=trip.route, stop=stop_b, sequence=2, offset_minutes=60)
        RouteStop.objects.create(route=trip.route, stop=stop_a, sequence=1, offset_minutes=0)

        client = APIClient()
        resp = client.get(f"/api/v1/trips/{trip.public_id}/stops/")

        assert resp.status_code == 200
        assert isinstance(resp.data, list)
        assert [s["sequence"] for s in resp.data] == [1, 2]
        assert resp.data[0]["stop"]["name_en"] == "Stop A"

    def test_unknown_trip_404s(self):
        client = APIClient()
        resp = client.get("/api/v1/trips/00000000-0000-0000-0000-000000000000/stops/")
        assert resp.status_code == 404


class TestSeatLayoutDesyncDetection:
    """
    Regression test for the demo dataset's known seat-layout desync (see
    CLAUDE.md's Status section, and apps/fleet/services.py's BusInUseError
    docstring for how a bus's seat_layout can drift out from under its
    already-created trips): a hold on such a trip must fail with a clean,
    translated 'seat_layout_desynced' error instead of the confusing
    'Unknown seats' 400 HoldSeatsView used to return.
    """

    def _desync_trip(self, trip, bus):
        other_layout = SeatLayout.objects.create(operator=bus.operator, name="Other Layout")
        Seat.objects.create(
            seat_layout=other_layout, seat_number="X1", deck=1, row_position=1, col_position=1
        )
        bus.seat_layout = other_layout
        bus.save(update_fields=["seat_layout"])

    def test_ensure_trip_bookable_raises_on_desync(self):
        trip, _seats = _make_trip(num_seats=2)
        self._desync_trip(trip, trip.bus)

        with pytest.raises(services.TripSeatLayoutDesyncedError):
            services.ensure_trip_bookable(trip)

    def test_hold_seats_endpoint_returns_clean_error_on_desync(self):
        trip, seats = _make_trip(num_seats=2)
        self._desync_trip(trip, trip.bus)

        client = APIClient()
        resp = client.post(
            f"/api/v1/trips/{trip.public_id}/hold/", {"seat_ids": [str(seats[0].public_id)]}
        )

        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "seat_layout_desynced"

    def test_hold_seats_endpoint_unaffected_when_layout_matches(self):
        trip, seats = _make_trip(num_seats=2)

        client = APIClient()
        resp = client.post(
            f"/api/v1/trips/{trip.public_id}/hold/", {"seat_ids": [str(seats[0].public_id)]}
        )

        assert resp.status_code == 201
