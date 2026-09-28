import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat

from . import services
from .models import Booking, BookingPassenger

pytestmark = pytest.mark.django_db


_plate_counter = itertools.count(1)


def _make_trip(num_seats=10, departure_in_hours=72, operator=None):
    operator = operator or Operator.objects.create(
        name="Test Operator", contact_phone="+85511111111", status="approved"
    )
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
    return trip, seats, operator


def _passenger(name="Sok Dara"):
    return {"full_name": name, "age": 30, "gender": "male", "phone": "+85512345678"}


def _hold_and_create_booking(trip, seats, n=1, **kwargs):
    seat_ids = [s.id for s in seats[:n]]
    token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest-session")
    passengers = [_passenger(f"Passenger {i}") for i in range(n)]
    defaults = {"contact_phone": "012345678"}
    defaults.update(kwargs)
    return services.create_booking(trip.id, token, passengers, **defaults)


class TestCreateBooking:
    def test_success_computes_fare_and_generates_pnr(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=2, currency=Booking.Currency.USD)

        assert len(booking.pnr) == 8
        assert booking.status == Booking.Status.PENDING_PAYMENT
        assert booking.subtotal_amount == Decimal("20.00")
        assert booking.fee_amount == Decimal("0.00")
        assert booking.total_amount == Decimal("20.00")
        assert booking.contact_phone == "+85512345678"
        assert booking.passengers.count() == 2

    def test_khr_currency_uses_khr_fare(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1, currency=Booking.Currency.KHR)
        assert booking.subtotal_amount == Decimal("41000.00")

    def test_rejects_expired_hold_token(self):
        trip, seats, _op = _make_trip()
        past = timezone.now() - timedelta(minutes=1)
        expired_token = trip_services._sign_hold_token(trip.id, [seats[0].id], past)

        with pytest.raises(trip_services.InvalidHoldTokenError):
            services.create_booking(
                trip.id, expired_token, [_passenger()], contact_phone="012345678"
            )

        # the seat must not have been touched
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE

    def test_rejects_tampered_hold_token(self):
        trip, seats, _op = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")

        with pytest.raises(trip_services.InvalidHoldTokenError):
            services.create_booking(trip.id, tampered, [_passenger()], contact_phone="012345678")

    def test_rejects_passenger_count_mismatch(self):
        trip, seats, _op = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id, seats[1].id], session_key="guest")

        with pytest.raises(services.InvalidPassengerCountError):
            services.create_booking(
                trip.id, token, [_passenger()], contact_phone="012345678"
            )

    def test_guest_checkout_has_no_user(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1)
        assert booking.user is None


class TestConfirmBooking:
    def test_confirm_flips_seats_to_booked_and_sets_qr_token(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1)

        confirmed = services.confirm_booking(booking)
        assert confirmed.status == Booking.Status.CONFIRMED
        assert confirmed.qr_token
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.BOOKED

    def test_cannot_confirm_twice(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.confirm_booking(booking)
        with pytest.raises(services.BookingStateError):
            services.confirm_booking(booking)


class TestCancelBookingRefundTiers:
    """Every tier from settings.CANCELLATION_REFUND_TIERS, exercised end to end."""

    def test_more_than_48h_before_departure_refunds_90_percent(self):
        trip, seats, _op = _make_trip(departure_in_hours=72)
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.confirm_booking(booking)

        cancelled = services.cancel_booking(booking)
        assert cancelled.refund_percentage == Decimal(90)
        assert cancelled.refund_amount == Decimal("9.00")

    def test_between_24_and_48h_refunds_50_percent(self):
        trip, seats, _op = _make_trip(departure_in_hours=30)
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.confirm_booking(booking)

        cancelled = services.cancel_booking(booking)
        assert cancelled.refund_percentage == Decimal(50)
        assert cancelled.refund_amount == Decimal("5.00")

    def test_less_than_24h_refunds_nothing(self):
        trip, seats, _op = _make_trip(departure_in_hours=5)
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.confirm_booking(booking)

        cancelled = services.cancel_booking(booking)
        assert cancelled.refund_percentage == Decimal(0)
        assert cancelled.refund_amount == Decimal("0.00")

    def test_cancel_confirmed_booking_frees_the_seat(self):
        trip, seats, _op = _make_trip(departure_in_hours=72)
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.confirm_booking(booking)

        services.cancel_booking(booking)
        trip.refresh_from_db()
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE
        assert trip.seats_available == len(seats)

    def test_cancel_pending_payment_booking_has_no_refund_but_frees_seat(self):
        trip, seats, _op = _make_trip(departure_in_hours=72)
        booking = _hold_and_create_booking(trip, seats, n=1)

        cancelled = services.cancel_booking(booking)
        assert cancelled.refund_amount == Decimal("0.00")
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE

    def test_a_cancelled_bookings_seat_can_be_booked_again(self):
        """Regression: BookingPassenger.trip_seat used to be a OneToOneField,
        so a seat freed by cancel_booking could never be booked again — the
        second create_booking's BookingPassenger insert hit a duplicate-key
        IntegrityError against the first (cancelled) booking's still-present
        row. Found live via the Django-templates checkout flow re-using a
        seat from an earlier payment-failed booking."""
        trip, seats, _op = _make_trip(departure_in_hours=72)
        first = _hold_and_create_booking(trip, seats, n=1)
        services.cancel_booking(first)

        second = _hold_and_create_booking(trip, seats, n=1)

        assert second.passengers.get().trip_seat_id == first.passengers.get().trip_seat_id
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.HELD

    def test_cannot_cancel_an_already_cancelled_booking(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1)
        services.cancel_booking(booking)
        with pytest.raises(services.BookingStateError):
            services.cancel_booking(booking)

    def test_cancel_error_message_is_translated_to_khmer_on_request(self):
        """
        Step 17 hardening: business-rule errors must not always come back in
        English — apps.bookings.services wraps them in gettext(), and this
        confirms the whole path (LocaleMiddleware reading Accept-Language +
        the compiled locale/km/LC_MESSAGES/django.mo catalog) actually
        produces Khmer text for a real HTTP error response, not just that
        the service layer *can* translate in isolation.
        """
        trip, seats, _op = _make_trip()
        user = User.objects.create_user(email="user18888888@bbms.test", password="Str0ngPassw0rd!")
        booking = _hold_and_create_booking(trip, seats, n=1, user=user)
        services.cancel_booking(booking)

        client = APIClient()
        client.force_authenticate(user)
        resp = client.post(
            f"/api/v1/bookings/{booking.public_id}/cancel/", HTTP_ACCEPT_LANGUAGE="km"
        )

        assert resp.status_code == 400
        message = resp.data["error"]["message"]
        assert booking.pnr in message
        assert message != f"Booking {booking.pnr} is cancelled and cannot be cancelled."
        assert any("ក" <= ch <= "៿" for ch in message), "expected Khmer script in the message"


class TestRescheduleBooking:
    def test_reschedule_moves_seats_and_charges_fare_difference_plus_fee(self):
        trip, seats, operator = _make_trip(departure_in_hours=72)
        booking = _hold_and_create_booking(trip, seats, n=1, currency=Booking.Currency.USD)
        services.confirm_booking(booking)

        new_trip, new_seats, _ = _make_trip(departure_in_hours=96, operator=operator)
        Trip.objects.filter(id=new_trip.id).update(base_fare_usd="15.00")
        new_trip.refresh_from_db()
        new_token = trip_services.hold_seats(new_trip.id, [new_seats[0].id], session_key="guest-session")

        rescheduled = services.reschedule_booking(booking, new_trip.id, new_token)

        assert rescheduled.trip_id == new_trip.id
        assert rescheduled.previous_trip_id == trip.id
        assert rescheduled.fare_difference_amount == Decimal("5.00")
        assert rescheduled.reschedule_fee_amount == Decimal("1.00")
        assert rescheduled.total_amount == Decimal("10.00") + Decimal("5.00") + Decimal("1.00")

        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE
        assert TripSeat.objects.get(trip=new_trip, seat=new_seats[0]).status == TripSeat.Status.BOOKED

        passenger = BookingPassenger.objects.get(booking=booking)
        assert passenger.trip_seat.trip_id == new_trip.id

    def test_cannot_reschedule_a_pending_payment_booking(self):
        trip, seats, operator = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1)

        new_trip, new_seats, _ = _make_trip(operator=operator)
        new_token = trip_services.hold_seats(new_trip.id, [new_seats[0].id], session_key="guest-session")

        with pytest.raises(services.BookingStateError):
            services.reschedule_booking(booking, new_trip.id, new_token)


class TestGuestLookupApi:
    def test_lookup_with_correct_pnr_and_phone_succeeds(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1, contact_phone="012345678")

        client = APIClient()
        resp = client.get(f"/api/v1/bookings/lookup/?pnr={booking.pnr}&phone=012345678")
        assert resp.status_code == 200
        assert resp.data["pnr"] == booking.pnr

    def test_lookup_with_wrong_phone_fails(self):
        trip, seats, _op = _make_trip()
        booking = _hold_and_create_booking(trip, seats, n=1, contact_phone="012345678")

        client = APIClient()
        resp = client.get(f"/api/v1/bookings/lookup/?pnr={booking.pnr}&phone=099999999")
        assert resp.status_code == 404

    def test_lookup_with_wrong_pnr_fails(self):
        trip, seats, _op = _make_trip()
        _hold_and_create_booking(trip, seats, n=1, contact_phone="012345678")

        client = APIClient()
        resp = client.get("/api/v1/bookings/lookup/?pnr=ZZZZZZZZ&phone=012345678")
        assert resp.status_code == 404


class TestCreateBookingApi:
    def test_guest_can_create_booking_via_api(self):
        trip, seats, _op = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest-session")

        client = APIClient()
        resp = client.post(
            "/api/v1/bookings/",
            {
                "trip_id": str(trip.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
                "currency": "USD",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["status"] == Booking.Status.PENDING_PAYMENT


class TestAgentAssistedBooking:
    def _counter_agent(self, operator):
        user = User.objects.create_user(
            email="user19999999@bbms.test", password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
        )
        OperatorStaff.objects.create(
            user=user,
            operator=operator,
            staff_role=OperatorStaff.StaffRole.COUNTER_AGENT,
            can_create_bookings=True,
        )
        return user

    def test_counter_agent_can_book_for_a_walk_in(self):
        trip, seats, operator = _make_trip()
        agent = self._counter_agent(operator)
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="counter-session")

        client = APIClient()
        client.force_authenticate(agent)
        resp = client.post(
            "/api/v1/bookings/agent/",
            {
                "trip_id": str(trip.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
                "currency": "USD",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["booking_channel"] == Booking.Channel.COUNTER

        booking = Booking.objects.get(pnr=resp.data["pnr"])
        assert booking.booked_by_staff_id == agent.id

    def test_non_counter_staff_cannot_use_agent_endpoint(self):
        trip, seats, _op = _make_trip()
        passenger_user = User.objects.create_user(email="user18888888@bbms.test", password="Str0ngPassw0rd!")
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="s")

        client = APIClient()
        client.force_authenticate(passenger_user)
        resp = client.post(
            "/api/v1/bookings/agent/",
            {
                "trip_id": str(trip.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_agent_cannot_book_another_operators_trip(self):
        trip_a, seats_a, operator_a = _make_trip()
        _trip_b, _seats_b, operator_b = _make_trip()
        agent_b = self._counter_agent(operator_b)
        token = trip_services.hold_seats(trip_a.id, [seats_a[0].id], session_key="s")

        client = APIClient()
        client.force_authenticate(agent_b)
        resp = client.post(
            "/api/v1/bookings/agent/",
            {
                "trip_id": str(trip_a.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
            },
            format="json",
        )
        assert resp.status_code == 403


class TestCancellationPreviewApi:
    def _owned_booking(self, **kwargs):
        trip, seats, operator = _make_trip(departure_in_hours=72)
        user = User.objects.create_user(email="user17777777@bbms.test", password="Str0ngPassw0rd!")
        booking = _hold_and_create_booking(trip, seats, n=1, user=user, **kwargs)
        return trip, seats, operator, user, booking

    def test_preview_matches_the_real_cancel_and_does_not_mutate(self):
        trip, seats, _op, user, booking = self._owned_booking()
        services.confirm_booking(booking)

        client = APIClient()
        client.force_authenticate(user)
        preview = client.get(f"/api/v1/bookings/{booking.public_id}/cancellation-preview/")
        assert preview.status_code == 200
        assert preview.data["refund_percentage"] == "90.00"
        assert preview.data["refund_amount"] == "9.00"

        booking.refresh_from_db()
        assert booking.status == Booking.Status.CONFIRMED
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.BOOKED

        cancelled = client.post(f"/api/v1/bookings/{booking.public_id}/cancel/")
        assert cancelled.data["refund_percentage"] == preview.data["refund_percentage"]
        assert cancelled.data["refund_amount"] == preview.data["refund_amount"]

    def test_preview_is_zero_for_a_pending_payment_booking(self):
        _trip, _seats, _op, user, booking = self._owned_booking()

        client = APIClient()
        client.force_authenticate(user)
        preview = client.get(f"/api/v1/bookings/{booking.public_id}/cancellation-preview/")
        assert preview.status_code == 200
        assert preview.data["refund_amount"] == "0.00"

    def test_another_user_cannot_preview_someone_elses_booking(self):
        _trip, _seats, _op, _user, booking = self._owned_booking()
        other_user = User.objects.create_user(email="user16666666@bbms.test", password="Str0ngPassw0rd!")

        client = APIClient()
        client.force_authenticate(other_user)
        resp = client.get(f"/api/v1/bookings/{booking.public_id}/cancellation-preview/")
        assert resp.status_code == 404


class TestReschedulePreviewApi:
    def test_preview_matches_the_real_reschedule_and_does_not_mutate(self):
        trip, seats, operator = _make_trip(departure_in_hours=72)
        user = User.objects.create_user(email="user15555555@bbms.test", password="Str0ngPassw0rd!")
        booking = _hold_and_create_booking(trip, seats, n=1, user=user, currency=Booking.Currency.USD)
        services.confirm_booking(booking)

        new_trip, new_seats, _ = _make_trip(departure_in_hours=96, operator=operator)
        Trip.objects.filter(id=new_trip.id).update(base_fare_usd="15.00")
        new_trip.refresh_from_db()

        client = APIClient()
        client.force_authenticate(user)
        preview = client.get(
            f"/api/v1/bookings/{booking.public_id}/reschedule-preview/",
            {"new_trip_id": str(new_trip.public_id)},
        )
        assert preview.status_code == 200
        assert preview.data["fare_difference_amount"] == "5.00"
        assert preview.data["reschedule_fee_amount"] == "1.00"

        booking.refresh_from_db()
        assert booking.trip_id == trip.id  # unmutated
        assert TripSeat.objects.get(trip=new_trip, seat=new_seats[0]).status == TripSeat.Status.AVAILABLE

        new_token = trip_services.hold_seats(new_trip.id, [new_seats[0].id], session_key="guest-session")
        rescheduled = client.post(
            f"/api/v1/bookings/{booking.public_id}/reschedule/",
            {"new_trip_id": str(new_trip.public_id), "new_hold_token": new_token},
            format="json",
        )
        assert rescheduled.data["fare_difference_amount"] == preview.data["fare_difference_amount"]
        assert rescheduled.data["reschedule_fee_amount"] == preview.data["reschedule_fee_amount"]
