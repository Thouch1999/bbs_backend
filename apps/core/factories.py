"""
Plain test-data builders shared by the back-office test modules (Step 15+).
Not a pytest module (no test_ prefix), so it's never collected on its own.
Earlier steps' tests keep their own local helpers; this exists so the
operator/back-office tests don't each re-implement an operator + fleet +
trip + paid booking from scratch.
"""

import itertools
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.bookings import services as booking_services
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.payments.models import Payment
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip

_counter = itertools.count(1)


def _n() -> int:
    return next(_counter)


def make_operator(*, status=Operator.Status.APPROVED, commission_rate="10.00") -> Operator:
    return Operator.objects.create(
        name=f"Operator {_n()}",
        contact_phone="+85511111111",
        status=status,
        commission_rate=Decimal(commission_rate),
    )


def make_staff(operator, *, staff_role=OperatorStaff.StaffRole.OWNER, **perms) -> User:
    user = User.objects.create_user(
        email=f"staff{_n()}@bbms.test",
        password="Str0ngPassw0rd!",
        role=User.Role.OPERATOR_STAFF,
        full_name="Staff",
    )
    defaults = {"can_create_bookings": staff_role in ("owner", "manager", "counter_agent")}
    defaults["can_validate_tickets"] = staff_role == "conductor"
    defaults.update(perms)
    OperatorStaff.objects.create(user=user, operator=operator, staff_role=staff_role, **defaults)
    return user


def client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def make_layout(operator, *, num_seats=4) -> tuple[SeatLayout, list[Seat]]:
    layout = SeatLayout.objects.create(operator=operator, name=f"Layout {_n()}")
    Seat.objects.bulk_create(
        [
            Seat(seat_layout=layout, seat_number=f"A{i}", deck=1, row_position=i, col_position=1)
            for i in range(1, num_seats + 1)
        ]
    )
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there) — refetch so seats[i].id is always usable.
    seats = list(Seat.objects.filter(seat_layout=layout).order_by("id"))
    return layout, seats


def make_bus(operator, *, layout=None, num_seats=4) -> Bus:
    if layout is None:
        layout, _ = make_layout(operator, num_seats=num_seats)
    return Bus.objects.create(operator=operator, seat_layout=layout, plate_number=f"PP-{_n():04d}")


def make_route(operator, *, duration=300) -> Route:
    origin = City.objects.create(name_en=f"Origin {_n()}", name_km="ក")
    destination = City.objects.create(name_en=f"Destination {_n()}", name_km="ខ")
    return Route.objects.create(
        operator=operator,
        origin_city=origin,
        destination_city=destination,
        name=f"Route {_n()}",
        estimated_duration_minutes=duration,
    )


def make_trip(operator, *, num_seats=4, departure_in_hours=72, route=None, bus=None, **fields) -> Trip:
    route = route or make_route(operator)
    bus = bus or make_bus(operator, num_seats=num_seats)
    departure_at = fields.pop("departure_at", timezone.now() + timedelta(hours=departure_in_hours))
    trip = Trip.objects.create(
        route=route,
        bus=bus,
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=5),
        base_fare_usd=Decimal("10.00"),
        base_fare_khr=Decimal(41000),
        **fields,
    )
    trip_services.generate_trip_seats(trip)
    return trip


def paid_booking(trip, *, n=1, currency="USD", provider=Payment.Provider.MOCK, contact_phone="012345678"):
    seat_ids = list(
        trip.trip_seats.filter(status="available").order_by("seat_id").values_list("seat_id", flat=True)[:n]
    )
    token = trip_services.hold_seats(trip.id, seat_ids, session_key=f"guest-{_n()}")
    passengers = [{"full_name": f"Passenger {i}"} for i in range(n)]
    booking = booking_services.create_booking(
        trip.id, token, passengers, contact_phone=contact_phone, currency=currency
    )
    booking_services.confirm_booking(booking)
    Payment.objects.create(
        booking=booking,
        provider=provider,
        status=Payment.Status.SUCCEEDED,
        amount=booking.total_amount,
        currency=booking.currency,
    )
    return booking
