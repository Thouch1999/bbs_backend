import itertools
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.bookings import services as booking_services
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip

from . import services
from .models import BoardingRecord

pytestmark = pytest.mark.django_db

_plate_counter = itertools.count(1)


def _make_trip(num_seats=4, departure_in_hours=2, operator=None):
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


def _confirmed_booking(trip, seats, n=1):
    seat_ids = [s.id for s in seats[:n]]
    token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest")
    passengers = [{"full_name": f"Passenger {i}", "age": 30} for i in range(n)]
    booking = booking_services.create_booking(
        trip.id, token, passengers, contact_phone="012345678"
    )
    return booking_services.confirm_booking(booking)


def _conductor(operator, can_validate_tickets=True):
    user = User.objects.create_user(
        email="user19999999@bbms.test", password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
    )
    OperatorStaff.objects.create(
        user=user,
        operator=operator,
        staff_role=OperatorStaff.StaffRole.CONDUCTOR,
        can_validate_tickets=can_validate_tickets,
    )
    return user


class TestValidateQrAndBoard:
    def test_valid_qr_boards_every_passenger_on_the_booking(self):
        trip, seats, _op = _make_trip(num_seats=2)
        booking = _confirmed_booking(trip, seats, n=2)
        conductor = _conductor(booking.trip.route.operator)

        records = services.validate_qr_and_board(booking.qr_token, trip.id, scanned_by=conductor)

        assert len(records) == 2
        assert all(r.status == BoardingRecord.Status.BOARDED for r in records)
        assert BoardingRecord.objects.filter(booking=booking).count() == 2

    def test_rescan_of_an_already_boarded_booking_is_rejected(self):
        trip, seats, _op = _make_trip()
        booking = _confirmed_booking(trip, seats)
        conductor = _conductor(booking.trip.route.operator)
        services.validate_qr_and_board(booking.qr_token, trip.id, scanned_by=conductor)

        with pytest.raises(services.AlreadyBoardedError):
            services.validate_qr_and_board(booking.qr_token, trip.id, scanned_by=conductor)

    def test_wrong_trip_is_rejected(self):
        trip, seats, operator = _make_trip()
        booking = _confirmed_booking(trip, seats)
        other_trip, _other_seats, _ = _make_trip(operator=operator)
        conductor = _conductor(operator)

        with pytest.raises(services.WrongTripError):
            services.validate_qr_and_board(booking.qr_token, other_trip.id, scanned_by=conductor)

    def test_unconfirmed_booking_is_rejected(self):
        from apps.core.utils import sign_qr_token

        trip, seats, _op = _make_trip()
        seat_ids = [seats[0].id]
        token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest")
        booking = booking_services.create_booking(
            trip.id, token, [{"full_name": "Not Paid"}], contact_phone="012345678"
        )
        conductor = _conductor(booking.trip.route.operator)
        # confirm_booking is what normally signs qr_token — build the same
        # payload manually since this booking is deliberately left unconfirmed.
        unconfirmed_qr = sign_qr_token({"pnr": booking.pnr, "booking_id": booking.id})

        with pytest.raises(services.BookingNotConfirmedError):
            services.validate_qr_and_board(unconfirmed_qr, trip.id, scanned_by=conductor)

    def test_tampered_token_is_rejected(self):
        trip, seats, _op = _make_trip()
        booking = _confirmed_booking(trip, seats)
        conductor = _conductor(booking.trip.route.operator)
        tampered = booking.qr_token[:-1] + ("a" if booking.qr_token[-1] != "a" else "b")

        with pytest.raises(services.InvalidQrTokenError):
            services.validate_qr_and_board(tampered, trip.id, scanned_by=conductor)


class TestManifest:
    def test_manifest_lists_confirmed_passengers_with_boarded_flag(self):
        trip, seats, _op = _make_trip(num_seats=2)
        booking = _confirmed_booking(trip, seats, n=2)
        conductor = _conductor(booking.trip.route.operator)

        manifest = services.get_manifest(trip)
        assert len(manifest) == 2
        assert all(row["boarded"] is False for row in manifest)

        services.validate_qr_and_board(booking.qr_token, trip.id, scanned_by=conductor)
        manifest = services.get_manifest(trip)
        assert all(row["boarded"] is True for row in manifest)

    def test_manifest_excludes_pending_payment_bookings(self):
        trip, seats, _op = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        booking_services.create_booking(
            trip.id, token, [{"full_name": "Not Paid"}], contact_phone="012345678"
        )

        assert services.get_manifest(trip) == []


class TestBoardingApi:
    def test_conductor_can_validate_qr_via_api(self):
        trip, seats, operator = _make_trip()
        booking = _confirmed_booking(trip, seats)
        conductor = _conductor(operator)

        client = APIClient()
        client.force_authenticate(conductor)
        resp = client.post(
            f"/api/v1/boarding/trips/{trip.public_id}/validate-qr/", {"qr_token": booking.qr_token}
        )
        assert resp.status_code == 200
        assert resp.data["pnr"] == booking.pnr

    def test_non_conductor_staff_cannot_validate_qr(self):
        trip, seats, operator = _make_trip()
        booking = _confirmed_booking(trip, seats)
        staff = _conductor(operator, can_validate_tickets=False)

        client = APIClient()
        client.force_authenticate(staff)
        resp = client.post(
            f"/api/v1/boarding/trips/{trip.public_id}/validate-qr/", {"qr_token": booking.qr_token}
        )
        assert resp.status_code == 403

    def test_conductor_cannot_validate_another_operators_trip(self):
        trip_a, seats_a, _operator_a = _make_trip()
        booking = _confirmed_booking(trip_a, seats_a)
        _trip_b, _seats_b, operator_b = _make_trip()
        conductor_b = _conductor(operator_b)

        client = APIClient()
        client.force_authenticate(conductor_b)
        resp = client.post(
            f"/api/v1/boarding/trips/{trip_a.public_id}/validate-qr/", {"qr_token": booking.qr_token}
        )
        assert resp.status_code == 403

    def test_manifest_endpoint_returns_passenger_list(self):
        trip, seats, operator = _make_trip()
        _confirmed_booking(trip, seats)
        conductor = _conductor(operator)

        client = APIClient()
        client.force_authenticate(conductor)
        resp = client.get(f"/api/v1/boarding/trips/{trip.public_id}/manifest/")
        assert resp.status_code == 200
        assert len(resp.data) == 1
