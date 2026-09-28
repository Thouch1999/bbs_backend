import itertools
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import signing
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings import services as booking_services
from apps.bookings.models import Booking, BookingPassenger
from apps.core.models import AuditLog
from apps.core.utils import (
    _PNR_ALPHABET,
    generate_pnr,
    sign_qr_token,
    to_khr,
    to_usd,
    verify_qr_token,
)
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators import services as operator_services
from apps.operators.models import Operator
from apps.payments import services as payment_services
from apps.payments.models import Payment
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat


class TestGeneratePnr:
    def test_length_and_charset(self):
        code = generate_pnr(exists_fn=lambda c: False)
        assert len(code) == 8
        assert set(code) <= set(_PNR_ALPHABET)

    def test_excludes_ambiguous_characters(self):
        for _ in range(200):
            code = generate_pnr(exists_fn=lambda c: False)
            assert not set(code) & {"O", "0", "I", "1"}

    def test_retries_on_collision(self):
        seen = {"AAAAAAAA"}

        def exists_fn(code):
            if code in seen:
                return True
            seen.add(code)
            return False

        code = generate_pnr(exists_fn=exists_fn)
        assert code != "AAAAAAAA"

    def test_gives_up_after_persistent_collisions(self):
        with pytest.raises(RuntimeError):
            generate_pnr(exists_fn=lambda c: True)

    def test_many_calls_are_unique(self):
        codes = {generate_pnr(exists_fn=lambda c: False) for _ in range(500)}
        assert len(codes) == 500


class TestQrToken:
    def test_round_trip(self):
        payload = {"pnr": "AB3D9F2K", "booking_id": 42}
        token = sign_qr_token(payload)
        assert verify_qr_token(token) == payload

    def test_tampered_token_is_rejected(self):
        token = sign_qr_token({"pnr": "AB3D9F2K"})
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        with pytest.raises(signing.BadSignature):
            verify_qr_token(tampered)

    def test_expired_token_is_rejected(self):
        token = sign_qr_token({"pnr": "AB3D9F2K"})
        time.sleep(1.1)
        with pytest.raises(signing.SignatureExpired):
            verify_qr_token(token, max_age=1)

    def test_still_valid_within_max_age(self):
        token = sign_qr_token({"pnr": "AB3D9F2K"})
        assert verify_qr_token(token, max_age=60) == {"pnr": "AB3D9F2K"}


class TestMoneyConversion:
    def test_to_khr(self):
        from decimal import Decimal

        assert to_khr(Decimal("1.00"), rate=Decimal(4100)) == Decimal(4100)

    def test_to_usd(self):
        from decimal import Decimal

        assert to_usd(Decimal(4100), rate=Decimal(4100)) == Decimal("1.00")

    def test_round_trip_is_stable(self):
        usd = Decimal("12.34")
        rate = Decimal(4100)
        khr = to_khr(usd, rate=rate)
        back = to_usd(khr, rate=rate)
        assert abs(back - usd) < Decimal("0.01")


_plate_counter = itertools.count(1)


def _make_trip(operator, num_seats=1, departure_in_hours=72):
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
        operator=operator, seat_layout=layout, plate_number=f"PP-{next(_plate_counter):04d}"
    )
    departure_at = timezone.now() + timedelta(hours=departure_in_hours)
    trip = Trip.objects.create(
        route=route,
        bus=bus,
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=6),
        base_fare_usd="10.00",
        base_fare_khr="41000",
    )
    trip_services.generate_trip_seats(trip)
    return trip, seats


@pytest.mark.django_db
class TestAuditLog:
    """SRS 7.18: operator approval, refunds, booking cancellation and role
    changes must each leave an audit trail — written from services.py, so it
    can't be bypassed by whichever entry point triggers the action."""

    def test_operator_registration_logs_a_role_change(self):
        user = User.objects.create_user(email="user12121299@bbms.test", password="Str0ngPassw0rd!")
        operator_services.register_operator(user, name="Test Co", contact_phone="+85511111111")

        entry = AuditLog.objects.get(action=AuditLog.Action.ROLE_CHANGED, target_id=str(user.public_id))
        assert entry.actor_id == user.id
        assert entry.before == {"role": User.Role.PASSENGER}
        assert entry.after == {"role": User.Role.OPERATOR_STAFF}

    def test_operator_approval_is_logged(self):
        admin = User.objects.create_user(email="user12121298@bbms.test", password="Str0ngPassw0rd!")
        operator = Operator.objects.create(name="Test Co", contact_phone="+85511111111")

        operator_services.approve_operator(operator, admin)

        entry = AuditLog.objects.get(
            action=AuditLog.Action.OPERATOR_APPROVED, target_id=str(operator.public_id)
        )
        assert entry.actor_id == admin.id
        assert entry.after["status"] == Operator.Status.APPROVED

    def test_operator_rejection_is_logged(self):
        admin = User.objects.create_user(email="user12121297@bbms.test", password="Str0ngPassw0rd!")
        operator = Operator.objects.create(name="Test Co", contact_phone="+85511111111")

        operator_services.reject_operator(operator, admin, "Incomplete licence.")

        entry = AuditLog.objects.get(
            action=AuditLog.Action.OPERATOR_REJECTED, target_id=str(operator.public_id)
        )
        assert entry.after["reason"] == "Incomplete licence."

    def test_booking_cancellation_is_logged_with_the_acting_user(self):
        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, seats = _make_trip(operator)
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        booking = booking_services.create_booking(
            trip.id, token, [{"full_name": "Sok Dara"}], contact_phone="012345678"
        )
        passenger = User.objects.create_user(email="user12121296@bbms.test", password="Str0ngPassw0rd!")

        booking_services.cancel_booking(booking, reason="Change of plans", actor=passenger)

        entry = AuditLog.objects.get(
            action=AuditLog.Action.BOOKING_CANCELLED, target_id=str(booking.public_id)
        )
        assert entry.actor_id == passenger.id
        assert entry.after["reason"] == "Change of plans"

    def test_refund_is_logged_with_the_admin_actor(self):
        admin = User.objects.create_user(email="user12121295@bbms.test", password="Str0ngPassw0rd!")
        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, seats = _make_trip(operator)
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        booking = booking_services.create_booking(
            trip.id, token, [{"full_name": "Sok Dara"}], contact_phone="012345678"
        )
        booking_services.confirm_booking(booking)
        payment = Payment.objects.create(
            booking=booking,
            provider=Payment.Provider.MOCK,
            status=Payment.Status.SUCCEEDED,
            amount=booking.total_amount,
            currency=booking.currency,
        )

        refund = payment_services.create_refund(payment, Decimal("5.00"), reason="Goodwill", actor=admin)

        entry = AuditLog.objects.get(action=AuditLog.Action.REFUND_ISSUED, target_id=str(refund.public_id))
        assert entry.actor_id == admin.id
        assert entry.after["amount"] == "5.00"

@pytest.mark.django_db
class TestSeedDemoCommand:
    def test_creates_the_documented_dataset_shape(self):
        from django.core.management import call_command

        from apps.bookings.models import Booking
        from apps.fleet.models import Bus
        from apps.payments.models import PromoCode
        from apps.routes.models import Route

        call_command("seed_demo")

        assert Operator.objects.filter(status=Operator.Status.APPROVED).count() == 1
        assert Bus.objects.count() == 6
        assert Route.objects.count() == 8
        assert Trip.objects.count() == 60
        assert User.objects.filter(phone__startswith="+855100").count() == 10
        assert PromoCode.objects.count() == 2
        assert Booking.objects.count() > 0
        assert set(Booking.objects.values_list("status", flat=True)) >= {
            Booking.Status.CONFIRMED,
            Booking.Status.CANCELLED,
            Booking.Status.PENDING_PAYMENT,
        }
        # Booking requires a logged-in passenger account, so these demo
        # accounts must have a real, usable password to demo with.
        passenger = User.objects.get(email="passenger1@bbms.test")
        assert passenger.has_usable_password()
        assert passenger.check_password("Demo12345!")

    def test_is_safe_to_run_twice_in_a_row(self):
        from django.core.management import call_command

        from apps.bookings.models import Booking

        call_command("seed_demo")
        first_booking_count = Booking.objects.count()
        first_trip_count = Trip.objects.count()

        call_command("seed_demo")

        assert Trip.objects.count() == first_trip_count
        assert Booking.objects.count() == first_booking_count


@pytest.mark.django_db
class TestSystemTriggeredCancellation:
    def test_system_triggered_cancellation_has_no_actor(self):
        """A failed-payment webhook cancels the booking with no human actor."""
        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, seats = _make_trip(operator)
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        booking = booking_services.create_booking(
            trip.id, token, [{"full_name": "Sok Dara"}], contact_phone="012345678"
        )

        booking_services.cancel_booking(booking, reason="Payment failed via mock webhook.")

        entry = AuditLog.objects.get(
            action=AuditLog.Action.BOOKING_CANCELLED, target_id=str(booking.public_id)
        )
        assert entry.actor_id is None


class TestHealthChecks:
    """Step 18: /healthz never touches a dependency; /readyz genuinely
    checks the DB and Redis are reachable, not just that they're configured."""

    @pytest.mark.django_db
    def test_healthz_never_touches_the_database(self, client, django_assert_num_queries):
        with django_assert_num_queries(0):
            resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    @pytest.mark.django_db
    def test_readyz_is_ok_when_db_and_redis_are_reachable(self, client):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"] == {"database": True, "redis": True}

    @pytest.mark.django_db
    def test_readyz_reports_503_when_the_database_is_unreachable(self, client, monkeypatch):
        from django.db.utils import OperationalError

        from apps.core import views as core_views

        def _broken_cursor(*args, **kwargs):
            raise OperationalError("simulated DB outage")

        monkeypatch.setattr(core_views.connections["default"], "cursor", _broken_cursor)

        resp = client.get("/readyz")

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["database"] is False

    @pytest.mark.django_db
    def test_readyz_reports_503_when_redis_is_unreachable(self, client, monkeypatch):
        import redis as redis_module

        from apps.core import views as core_views

        def _broken_set(*args, **kwargs):
            raise redis_module.exceptions.ConnectionError("simulated Redis outage")

        monkeypatch.setattr(core_views.cache, "set", _broken_set)

        resp = client.get("/readyz")

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["redis"] is False
        assert body["checks"]["database"] is True


@pytest.mark.django_db
class TestRepairSeatLayoutDesyncCommand:
    """apps/core/management/commands/repair_seat_layout_desync.py — repairs
    the demo dataset's known desync (CLAUDE.md's Status section) without
    disturbing existing bookings."""

    def _desynced_trip_with_booking(self, operator):
        trip, old_seats = _make_trip(operator, num_seats=3)
        old_seat_by_number = {s.seat_number: s for s in old_seats}

        booked_ts = TripSeat.objects.get(trip=trip, seat=old_seat_by_number["A1"])
        booked_ts.status = TripSeat.Status.BOOKED
        booked_ts.save(update_fields=["status"])
        booking = Booking.objects.create(
            pnr="TESTPNR1",
            trip=trip,
            contact_phone="+85512345678",
            currency=Booking.Currency.USD,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            status=Booking.Status.CONFIRMED,
        )
        passenger = BookingPassenger.objects.create(
            booking=booking, trip_seat=booked_ts, full_name="Test Passenger"
        )

        # Now desync: the bus moves to a differently-shaped layout that only
        # has A1/A2 in common with the old one (no A3), same as the real
        # 45<->40 seat demo-data fossil.
        new_layout = SeatLayout.objects.create(operator=operator, name="New Layout")
        Seat.objects.bulk_create(
            [
                Seat(seat_layout=new_layout, seat_number="A1", deck=1, row_position=1, col_position=1),
                Seat(seat_layout=new_layout, seat_number="A2", deck=1, row_position=2, col_position=1),
            ]
        )
        trip.bus.seat_layout = new_layout
        trip.bus.save(update_fields=["seat_layout"])

        return trip, booking, passenger, booked_ts

    def test_dry_run_makes_no_changes(self):
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, _booking, _passenger, booked_ts = self._desynced_trip_with_booking(operator)
        before_seat_id = TripSeat.objects.get(id=booked_ts.id).seat_id

        call_command("repair_seat_layout_desync")

        assert TripSeat.objects.get(id=booked_ts.id).seat_id == before_seat_id
        assert (
            trip.trip_seats.exclude(seat__seat_layout_id=trip.bus.seat_layout_id).count() == 3
        )

    def test_apply_remaps_booked_seat_without_breaking_the_booking(self):
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, _booking, passenger, booked_ts = self._desynced_trip_with_booking(operator)

        call_command("repair_seat_layout_desync", "--apply")

        trip.refresh_from_db()
        passenger.refresh_from_db()
        booked_ts.refresh_from_db()

        # Same TripSeat row (booking_passenger's FK target never changes),
        # now pointing at the new layout's A1, still booked.
        assert passenger.trip_seat_id == booked_ts.id
        assert booked_ts.seat.seat_layout_id == trip.bus.seat_layout_id
        assert booked_ts.seat.seat_number == "A1"
        assert booked_ts.status == TripSeat.Status.BOOKED

        # Every remaining TripSeat now matches the bus's current layout.
        assert not trip.trip_seats.exclude(seat__seat_layout_id=trip.bus.seat_layout_id).exists()
        # A1, A2 exist in the new layout: A1 is the remapped booked seat,
        # A2 was regenerated as available. A3 has no equivalent in the new
        # layout, so it's simply gone (not a booked seat, safe to drop).
        assert trip.trip_seats.count() == 2
        assert trip.seats_available == 1

    def test_apply_reports_unresolved_booked_seat_with_no_equivalent(self):
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, old_seats = _make_trip(operator, num_seats=1)
        booked_ts = TripSeat.objects.get(trip=trip, seat=old_seats[0])
        booked_ts.status = TripSeat.Status.BOOKED
        booked_ts.save(update_fields=["status"])
        booking = Booking.objects.create(
            pnr="TESTPNR2",
            trip=trip,
            contact_phone="+85512345678",
            currency=Booking.Currency.USD,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            status=Booking.Status.CONFIRMED,
        )
        BookingPassenger.objects.create(booking=booking, trip_seat=booked_ts, full_name="Test Passenger")

        # New layout has no "A1" at all — the booked seat can't be remapped.
        new_layout = SeatLayout.objects.create(operator=operator, name="No Overlap Layout")
        Seat.objects.create(seat_layout=new_layout, seat_number="Z9", deck=1, row_position=1, col_position=1)
        trip.bus.seat_layout = new_layout
        trip.bus.save(update_fields=["seat_layout"])

        call_command("repair_seat_layout_desync", "--apply")

        # Left untouched: still booked, still pointing at the old seat, the
        # booking's FK still resolves.
        booked_ts.refresh_from_db()
        assert booked_ts.status == TripSeat.Status.BOOKED
        assert booked_ts.seat_id == old_seats[0].id
        assert booked_ts.booking_passenger.get().full_name == "Test Passenger"

    def test_apply_remaps_a_cancelled_bookings_now_available_seat(self):
        """Regression: a first version of this command deleted every
        status=available TripSeat as 'unreferenced', but cancelling a
        booking only frees the seat (flips it back to available) — the
        historical BookingPassenger row still points at it. Deleting it
        500'd on Django's own ProtectedError against the real dev DB."""
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, old_seats = _make_trip(operator, num_seats=2)
        cancelled_ts = TripSeat.objects.get(trip=trip, seat=old_seats[0])
        booking = Booking.objects.create(
            pnr="TESTPNR3",
            trip=trip,
            contact_phone="+85512345678",
            currency=Booking.Currency.USD,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            status=Booking.Status.CANCELLED,
        )
        BookingPassenger.objects.create(booking=booking, trip_seat=cancelled_ts, full_name="Cancelled Pax")
        # cancelled_ts.status stays AVAILABLE (cancellation frees the seat) —
        # it's the BookingPassenger FK, not the status, that must be honored.
        assert cancelled_ts.status == TripSeat.Status.AVAILABLE

        new_layout = SeatLayout.objects.create(operator=operator, name="New Layout")
        Seat.objects.bulk_create(
            [
                Seat(seat_layout=new_layout, seat_number="A1", deck=1, row_position=1, col_position=1),
                Seat(seat_layout=new_layout, seat_number="A2", deck=1, row_position=2, col_position=1),
            ]
        )
        trip.bus.seat_layout = new_layout
        trip.bus.save(update_fields=["seat_layout"])

        call_command("repair_seat_layout_desync", "--apply")

        cancelled_ts.refresh_from_db()
        assert cancelled_ts.seat.seat_layout_id == new_layout.id
        assert cancelled_ts.seat.seat_number == "A1"
        assert cancelled_ts.status == TripSeat.Status.AVAILABLE
        assert cancelled_ts.booking_passenger.get().full_name == "Cancelled Pax"

    def test_apply_is_safe_to_run_twice(self):
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, _booking, _passenger, _booked_ts = self._desynced_trip_with_booking(operator)

        call_command("repair_seat_layout_desync", "--apply")
        first_count = trip.trip_seats.count()

        call_command("repair_seat_layout_desync", "--apply")

        assert trip.trip_seats.count() == first_count

    def test_apply_is_safe_to_run_twice_with_an_unresolved_seat(self):
        """Regression: a trip with an unresolved seat stays in the
        candidate list on every run (its one bad seat never becomes
        current-layout), so a second --apply must not try to re-create the
        already-regenerated available seats and hit unique_trip_seat."""
        from django.core.management import call_command

        operator = Operator.objects.create(
            name="Test Co", contact_phone="+85511111111", status=Operator.Status.APPROVED
        )
        trip, old_seats = _make_trip(operator, num_seats=2)
        booked_ts = TripSeat.objects.get(trip=trip, seat=old_seats[0])
        booked_ts.status = TripSeat.Status.BOOKED
        booked_ts.save(update_fields=["status"])
        booking = Booking.objects.create(
            pnr="TESTPNR4",
            trip=trip,
            contact_phone="+85512345678",
            currency=Booking.Currency.USD,
            subtotal_amount=Decimal("10.00"),
            total_amount=Decimal("10.00"),
            status=Booking.Status.CONFIRMED,
        )
        BookingPassenger.objects.create(booking=booking, trip_seat=booked_ts, full_name="Test Passenger")

        # New layout has no "A1" (the booked seat, stays unresolved) but
        # does have "A2" (the available seat, gets regenerated).
        new_layout = SeatLayout.objects.create(operator=operator, name="Partial Overlap Layout")
        Seat.objects.create(seat_layout=new_layout, seat_number="A2", deck=1, row_position=1, col_position=1)
        trip.bus.seat_layout = new_layout
        trip.bus.save(update_fields=["seat_layout"])

        call_command("repair_seat_layout_desync", "--apply")
        first_count = trip.trip_seats.count()
        assert first_count == 2  # unresolved A1 (old layout) + regenerated A2 (new layout)

        call_command("repair_seat_layout_desync", "--apply")  # must not raise IntegrityError

        assert trip.trip_seats.count() == first_count
