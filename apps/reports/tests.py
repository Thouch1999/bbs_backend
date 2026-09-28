import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
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

from . import services

pytestmark = pytest.mark.django_db

_plate_counter = itertools.count(1)


def _make_operator(commission_rate="10.00"):
    return Operator.objects.create(
        name="Test Operator",
        contact_phone="+85511111111",
        status="approved",
        commission_rate=Decimal(commission_rate),
    )


def _make_trip(operator, num_seats=4, departure_in_hours=72):
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


def _paid_booking(trip, seats, n=1, provider=Payment.Provider.MOCK):
    seat_ids = [s.id for s in seats[:n]]
    token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest")
    passengers = [{"full_name": f"Passenger {i}"} for i in range(n)]
    booking = booking_services.create_booking(trip.id, token, passengers, contact_phone="012345678")
    booking_services.confirm_booking(booking)
    Payment.objects.create(
        booking=booking, provider=provider, status=Payment.Status.SUCCEEDED,
        amount=booking.total_amount, currency=booking.currency,
    )
    return booking


def _owner(operator):
    user = User.objects.create_user(
        email="user19999999@bbms.test", password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
    )
    OperatorStaff.objects.create(user=user, operator=operator, staff_role=OperatorStaff.StaffRole.OWNER)
    return user


def _admin():
    return User.objects.create_user(
        email="user17777777@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN, is_staff=True
    )


class TestOperatorRevenue:
    def test_groups_revenue_by_route(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator)
        _paid_booking(trip, seats, n=2)

        rows = services.operator_revenue(operator, group_by="route")
        assert len(rows) == 1
        assert rows[0]["route_public_id"] == trip.route.public_id
        assert rows[0]["total_amount"] == Decimal("20.00")
        assert rows[0]["payment_count"] == 1

    def test_groups_revenue_by_payment_method(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator)
        _paid_booking(trip, seats, provider=Payment.Provider.COUNTER)

        rows = services.operator_revenue(operator, group_by="payment_method")
        assert rows == [
            {
                "provider": Payment.Provider.COUNTER,
                "currency": "USD",
                "total_amount": Decimal("10.00"),
                "payment_count": 1,
                "commission_amount": Decimal("1.00"),  # 10% commission
                "net_amount": Decimal("9.00"),
            }
        ]

    def test_excludes_other_operators(self):
        operator_a = _make_operator()
        operator_b = _make_operator()
        trip_a, seats_a = _make_trip(operator_a)
        _paid_booking(trip_a, seats_a)
        trip_b, seats_b = _make_trip(operator_b)
        _paid_booking(trip_b, seats_b)

        rows = services.operator_revenue(operator_a, group_by="route")
        assert len(rows) == 1
        assert rows[0]["route_public_id"] == trip_a.route.public_id


class TestOperatorOccupancy:
    def test_occupancy_rate_reflects_booked_seats(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator, num_seats=4)
        _paid_booking(trip, seats, n=1)

        rows = services.operator_occupancy(operator)
        assert len(rows) == 1
        assert rows[0]["total_seats"] == 4
        assert rows[0]["booked_seats"] == 1
        assert rows[0]["occupancy_rate"] == Decimal("25.00")


class TestOperatorCancellationSummary:
    def test_summarizes_cancelled_bookings_and_refunds(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator, departure_in_hours=72)
        booking = _paid_booking(trip, seats)
        booking_services.cancel_booking(booking)

        summary = services.operator_cancellation_summary(operator)
        assert summary["cancelled_count"] == 1
        assert summary["total_refund_usd"] == Decimal("9.00")  # 90% tier at 72h out
        assert summary["total_refund_khr"] == Decimal("0.00")


class TestAdminReports:
    def test_platform_totals_counts_confirmed_bookings_and_passengers(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator, num_seats=4)
        _paid_booking(trip, seats, n=2)

        totals = services.admin_platform_totals()
        assert totals["total_bookings"] == 1
        assert totals["total_passengers"] == 2
        assert totals["revenue"] == {"usd": Decimal("20.00"), "khr": Decimal("0.00")}
        assert totals["commission"]["usd"] == Decimal("2.00")  # 10%

    def test_top_routes_orders_by_bookings_descending(self):
        # Since Step 16 a "platform route" is a city pair across operators,
        # ranked by bookings (revenue per currency can't be ranked together).
        operator = _make_operator()
        trip_a, seats_a = _make_trip(operator)
        _paid_booking(trip_a, seats_a[:1], n=1)
        trip_b, seats_b = _make_trip(operator, num_seats=4)
        _paid_booking(trip_b, seats_b[:1], n=1)
        _paid_booking(trip_b, seats_b[1:], n=2)

        rows = services.admin_top_routes()
        assert rows[0]["origin_city_name"] == trip_b.route.origin_city.name_en
        assert rows[0]["bookings_count"] == 2
        assert rows[0]["revenue_usd"] == Decimal("30.00")
        assert rows[0]["operators_count"] == 1

    def test_reconciliation_computes_commission_and_payable(self):
        operator = _make_operator(commission_rate="10.00")
        trip, seats = _make_trip(operator)
        _paid_booking(trip, seats)

        rows = services.admin_reconciliation()
        assert len(rows) == 1
        row = rows[0]
        assert row["platform_recorded"] == Decimal("10.00")
        assert row["gateway_settled"] == Decimal("10.00")
        assert row["commission_amount"] == Decimal("1.00")
        assert row["operator_payable"] == Decimal("9.00")

    def test_reconciliation_nets_out_refunds(self):
        operator = _make_operator(commission_rate="10.00")
        trip, seats = _make_trip(operator, departure_in_hours=72)
        booking = _paid_booking(trip, seats)
        booking_services.cancel_booking(booking)  # 90% refund tier at 72h out

        from apps.payments.models import Refund

        payment = Payment.objects.get(booking=booking)
        Refund.objects.create(
            payment=payment, amount=Decimal("9.00"), status=Refund.Status.SUCCEEDED
        )

        rows = services.admin_reconciliation()
        row = rows[0]
        assert row["refunded"] == Decimal("9.00")
        assert row["gateway_settled"] == Decimal("1.00")


class TestReportsPermissions:
    def test_operator_owner_can_view_revenue_report(self):
        operator = _make_operator()
        owner = _owner(operator)

        client = APIClient()
        client.force_authenticate(owner)
        resp = client.get("/api/v1/reports/operator/revenue/")
        assert resp.status_code == 200

    def test_non_operator_cannot_view_operator_reports(self):
        passenger = User.objects.create_user(email="user18888888@bbms.test", password="Str0ngPassw0rd!")
        client = APIClient()
        client.force_authenticate(passenger)
        resp = client.get("/api/v1/reports/operator/revenue/")
        assert resp.status_code == 403

    def test_admin_can_view_platform_totals(self):
        client = APIClient()
        client.force_authenticate(_admin())
        resp = client.get("/api/v1/reports/admin/totals/")
        assert resp.status_code == 200

    def test_non_admin_cannot_view_platform_totals(self):
        operator = _make_operator()
        owner = _owner(operator)
        client = APIClient()
        client.force_authenticate(owner)
        resp = client.get("/api/v1/reports/admin/totals/")
        assert resp.status_code == 403


class TestManifestExport:
    def test_csv_export(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator)
        _paid_booking(trip, seats)
        owner = _owner(operator)

        client = APIClient()
        client.force_authenticate(owner)
        resp = client.get(
            f"/api/v1/reports/operator/trips/{trip.public_id}/manifest/export/?export_format=csv"
        )
        assert resp.status_code == 200
        assert resp["Content-Type"] == "text/csv"
        assert b"Passenger 0" in resp.content

    def test_pdf_export(self):
        operator = _make_operator()
        trip, seats = _make_trip(operator)
        _paid_booking(trip, seats)
        owner = _owner(operator)

        client = APIClient()
        client.force_authenticate(owner)
        resp = client.get(
            f"/api/v1/reports/operator/trips/{trip.public_id}/manifest/export/?export_format=pdf"
        )
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_cannot_export_another_operators_trip(self):
        operator_a = _make_operator()
        operator_b = _make_operator()
        trip_a, seats_a = _make_trip(operator_a)
        _paid_booking(trip_a, seats_a)
        owner_b = _owner(operator_b)

        client = APIClient()
        client.force_authenticate(owner_b)
        resp = client.get(f"/api/v1/reports/operator/trips/{trip_a.public_id}/manifest/export/")
        assert resp.status_code == 403
