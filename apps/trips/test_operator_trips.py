"""Step 15: operator trip scheduling, status changes and public-search fixes."""

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import OperatorStaff
from apps.bookings.models import Booking
from apps.core.factories import (
    client_for,
    make_bus,
    make_operator,
    make_route,
    make_staff,
    make_trip,
    paid_booking,
)
from apps.fleet.models import Bus
from apps.notifications.models import Notification
from apps.operators.models import Operator
from apps.trips import services
from apps.trips.models import Trip, TripSeat

pytestmark = pytest.mark.django_db


def _trip_payload(route, bus, departure_at, **extra):
    return {
        "route_id": str(route.public_id),
        "bus_id": str(bus.public_id),
        "departure_at": departure_at.isoformat(),
        "arrival_at": (departure_at + timedelta(hours=5)).isoformat(),
        "base_fare_usd": "10.00",
        "base_fare_khr": "41000",
        **extra,
    }


class TestOperatorTripObjectPermissions:
    """Regression: IsOperatorOwnerOrManager.has_object_permission read
    obj.operator_id, which Trip doesn't have — every detail/status call
    403'd for the trip's own operator."""

    def test_owner_can_retrieve_and_change_status_of_own_trip(self):
        operator = make_operator()
        trip = make_trip(operator)
        client = client_for(make_staff(operator))

        assert client.get(f"/api/v1/operator/trips/{trip.public_id}/").status_code == 200
        resp = client.post(
            f"/api/v1/operator/trips/{trip.public_id}/status/",
            {"status": "delayed", "delay_minutes": 30},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "delayed"

    def test_other_operator_gets_404_not_the_trip(self):
        trip = make_trip(make_operator())
        client = client_for(make_staff(make_operator()))
        assert client.get(f"/api/v1/operator/trips/{trip.public_id}/").status_code == 404


class TestOperatorTripRoles:
    def test_counter_agent_can_list_but_not_create(self):
        operator = make_operator()
        make_trip(operator)
        agent = client_for(make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT))

        assert agent.get("/api/v1/operator/trips/").status_code == 200
        route, bus = make_route(operator), make_bus(operator)
        resp = agent.post(
            "/api/v1/operator/trips/",
            _trip_payload(route, bus, timezone.now() + timedelta(days=2)),
            format="json",
        )
        assert resp.status_code == 403

    def test_pending_operator_cannot_schedule(self):
        operator = make_operator(status=Operator.Status.PENDING)
        route, bus = make_route(operator), make_bus(operator)
        client = client_for(make_staff(operator))
        resp = client.post(
            "/api/v1/operator/trips/",
            _trip_payload(route, bus, timezone.now() + timedelta(days=2)),
            format="json",
        )
        assert resp.status_code == 403
        assert client.get("/api/v1/operator/trips/").status_code == 200


class TestCreateAndUpdateTrip:
    def test_create_generates_seats_and_returns_stats(self):
        operator = make_operator()
        route, bus = make_route(operator), make_bus(operator, num_seats=6)
        conductor = make_staff(operator, staff_role=OperatorStaff.StaffRole.CONDUCTOR)
        client = client_for(make_staff(operator))

        resp = client.post(
            "/api/v1/operator/trips/",
            _trip_payload(
                route,
                bus,
                timezone.now() + timedelta(days=2),
                driver_name="Sok",
                conductor_id=str(conductor.operator_staff.public_id),
            ),
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["seats_total"] == 6
        assert resp.data["seats_available"] == 6
        assert resp.data["seats_sold"] == 0
        assert resp.data["driver_name"] == "Sok"
        assert resp.data["conductor_id"] == str(conductor.operator_staff.public_id)

    def test_conductor_must_be_allowed_to_validate_tickets(self):
        operator = make_operator()
        route, bus = make_route(operator), make_bus(operator)
        agent = make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/",
            _trip_payload(
                route,
                bus,
                timezone.now() + timedelta(days=2),
                conductor_id=str(agent.operator_staff.public_id),
            ),
            format="json",
        )
        assert resp.status_code == 400
        assert "conductor_id" in resp.data["error"]["details"]

    def test_overlapping_trip_on_same_bus_is_rejected(self):
        operator = make_operator()
        existing = make_trip(operator, departure_in_hours=48)
        client = client_for(make_staff(operator))
        resp = client.post(
            "/api/v1/operator/trips/",
            _trip_payload(existing.route, existing.bus, existing.departure_at + timedelta(hours=2)),
            format="json",
        )
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "bus_conflict"
        assert resp.data["error"]["details"]["conflicting_trip_ids"] == [str(existing.public_id)]

    def test_cancelled_trip_does_not_block_the_bus(self):
        operator = make_operator()
        existing = make_trip(operator, departure_in_hours=48, status=Trip.Status.CANCELLED)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/",
            _trip_payload(existing.route, existing.bus, existing.departure_at),
            format="json",
        )
        assert resp.status_code == 201

    def test_bus_in_maintenance_cannot_be_scheduled(self):
        operator = make_operator()
        route, bus = make_route(operator), make_bus(operator)
        Bus.objects.filter(pk=bus.pk).update(status=Bus.Status.MAINTENANCE)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/",
            _trip_payload(route, bus, timezone.now() + timedelta(days=2)),
            format="json",
        )
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "trip_schedule_error"

    def test_changing_bus_regenerates_seats_when_nothing_sold(self):
        operator = make_operator()
        trip = make_trip(operator, num_seats=4)
        bigger_bus = make_bus(operator, num_seats=9)
        resp = client_for(make_staff(operator)).patch(
            f"/api/v1/operator/trips/{trip.public_id}/", {"bus_id": str(bigger_bus.public_id)}, format="json"
        )
        assert resp.status_code == 200, resp.data
        trip.refresh_from_db()
        assert trip.bus_id == bigger_bus.id
        assert trip.trip_seats.count() == 9
        assert trip.seats_available == 9
        assert set(trip.trip_seats.values_list("seat__seat_layout_id", flat=True)) == {
            bigger_bus.seat_layout_id
        }

    def test_changing_bus_is_refused_once_seats_are_sold(self):
        operator = make_operator()
        trip = make_trip(operator)
        paid_booking(trip)
        resp = client_for(make_staff(operator)).patch(
            f"/api/v1/operator/trips/{trip.public_id}/",
            {"bus_id": str(make_bus(operator).public_id)},
            format="json",
        )
        assert resp.status_code == 409
        assert trip.trip_seats.count() == 4  # untouched

    def test_deleting_a_trip_with_bookings_is_a_conflict_not_a_500(self):
        operator = make_operator()
        trip = make_trip(operator)
        paid_booking(trip)
        client = client_for(make_staff(operator))
        client.raise_request_exception = False
        resp = client.delete(f"/api/v1/operator/trips/{trip.public_id}/")
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "in_use"


class TestRecurringTrips:
    def test_plan_honours_weekdays_in_local_time(self):
        departures = services.plan_recurring_departures(
            start_date=date(2030, 1, 7),  # a Monday
            end_date=date(2030, 1, 13),
            weekdays=[0, 4],  # Mon, Fri
            departure_time=time(7, 30),
            duration_minutes=300,
        )
        local = [timezone.localtime(d) for d, _ in departures]
        assert [d.date() for d in local] == [date(2030, 1, 7), date(2030, 1, 11)]
        assert all(d.time() == time(7, 30) for d in local)
        assert departures[0][1] - departures[0][0] == timedelta(minutes=300)

    def test_endpoint_creates_all_with_peak_pricing(self):
        operator = make_operator()
        route, bus = make_route(operator, duration=240), make_bus(operator, num_seats=3)
        start = timezone.localdate() + timedelta(days=10)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/recurring/",
            {
                "route_id": str(route.public_id),
                "bus_id": str(bus.public_id),
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=4)).isoformat(),
                "weekdays": [],
                "departure_time": "08:00",
                "base_fare_usd": "10.00",
                "base_fare_khr": "41000",
                "peak_start_date": (start + timedelta(days=3)).isoformat(),
                "peak_end_date": (start + timedelta(days=4)).isoformat(),
                "peak_multiplier": "1.50",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.data
        assert len(resp.data) == 5
        fares = [row["base_fare_usd"] for row in resp.data]
        assert fares == ["10.00", "10.00", "10.00", "15.00", "15.00"]
        assert resp.data[-1]["base_fare_khr"] == "61500"
        assert all(row["seats_total"] == 3 for row in resp.data)
        first = resp.data[0]
        assert timezone.datetime.fromisoformat(first["arrival_at"]) - timezone.datetime.fromisoformat(
            first["departure_at"]
        ) == timedelta(minutes=240)  # defaulted from the route

    def test_one_conflict_creates_nothing(self):
        operator = make_operator()
        route, bus = make_route(operator), make_bus(operator)
        start = timezone.localdate() + timedelta(days=10)
        tz = timezone.get_current_timezone()
        clash = timezone.make_aware(timezone.datetime.combine(start + timedelta(days=2), time(9, 0)), tz)
        make_trip(operator, route=route, bus=bus, departure_at=clash)

        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/recurring/",
            {
                "route_id": str(route.public_id),
                "bus_id": str(bus.public_id),
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=4)).isoformat(),
                "departure_time": "08:00",
                "base_fare_usd": "10.00",
                "base_fare_khr": "41000",
            },
            format="json",
        )
        assert resp.status_code == 409
        assert Trip.objects.filter(bus=bus).count() == 1

    def test_peak_needs_all_three_fields(self):
        operator = make_operator()
        route, bus = make_route(operator), make_bus(operator)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/trips/recurring/",
            {
                "route_id": str(route.public_id),
                "bus_id": str(bus.public_id),
                "start_date": "2030-01-01",
                "end_date": "2030-01-02",
                "departure_time": "08:00",
                "base_fare_usd": "10.00",
                "base_fare_khr": "41000",
                "peak_multiplier": "1.5",
            },
            format="json",
        )
        assert resp.status_code == 400


class TestTripStatus:
    def test_back_on_time_only_from_delayed(self):
        operator = make_operator()
        trip = make_trip(operator)
        client = client_for(make_staff(operator))
        url = f"/api/v1/operator/trips/{trip.public_id}/status/"

        assert client.post(url, {"status": "scheduled"}, format="json").status_code == 400
        client.post(url, {"status": "delayed", "delay_minutes": 20}, format="json")
        resp = client.post(url, {"status": "scheduled"}, format="json")
        assert resp.status_code == 200
        assert resp.data["status"] == "scheduled"
        assert resp.data["delay_minutes"] is None

    def test_cancelled_is_final(self):
        operator = make_operator()
        trip = make_trip(operator)
        client = client_for(make_staff(operator))
        url = f"/api/v1/operator/trips/{trip.public_id}/status/"
        assert client.post(url, {"status": "cancelled"}, format="json").status_code == 200
        resp = client.post(url, {"status": "delayed", "delay_minutes": 5}, format="json")
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "trip_status_error"

    def test_preview_matches_what_is_actually_sent(self):
        operator = make_operator()
        trip = make_trip(operator, num_seats=6)
        paid_booking(trip, n=2)  # 1 booking, 2 passengers
        with_email = paid_booking(trip, n=1)
        Booking.objects.filter(pk=with_email.pk).update(contact_email="rider@example.com")
        client = client_for(make_staff(operator))

        preview = client.get(
            f"/api/v1/operator/trips/{trip.public_id}/status-preview/",
            {"status": "delayed", "delay_minutes": 45},
        )
        assert preview.status_code == 200
        assert preview.data["bookings_count"] == 2
        assert preview.data["passengers_count"] == 3
        assert preview.data["channels"] == {"sms": 2, "email": 1, "telegram": 0}
        assert "45" in preview.data["messages"]["en"]
        assert set(preview.data["messages"]) == {"km", "en"}
        assert Notification.objects.filter(template="trip_delayed").count() == 0  # preview sent nothing

        before = Notification.objects.count()
        client.post(
            f"/api/v1/operator/trips/{trip.public_id}/status/",
            {"status": "delayed", "delay_minutes": 45},
            format="json",
        )
        sent = Notification.objects.filter(template=Notification.Template.TRIP_DELAYED)
        assert Notification.objects.count() - before == sum(preview.data["channels"].values()) == sent.count()


class TestOperatorTripStats:
    def test_revenue_is_per_currency_and_not_fanned_out_by_seats(self):
        operator = make_operator()
        trip = make_trip(operator, num_seats=6)
        paid_booking(trip, n=2, currency="USD")  # 20.00 USD
        paid_booking(trip, n=1, currency="KHR")  # 41000 KHR

        row = services.with_operator_stats(Trip.objects.filter(pk=trip.pk)).get()
        assert row.seats_total == 6
        assert row.seats_sold == 3
        assert row.bookings_count == 2
        assert row.revenue_usd == Decimal("20.00")
        assert row.revenue_khr == Decimal("41000.00")

    def test_list_filters_by_local_date_and_status(self):
        operator = make_operator()
        soon = make_trip(operator, departure_in_hours=30)
        make_trip(operator, departure_in_hours=24 * 8)
        day = timezone.localtime(soon.departure_at).date().isoformat()
        client = client_for(make_staff(operator))

        resp = client.get("/api/v1/operator/trips/", {"date_from": day, "date_to": day})
        assert [row["public_id"] for row in resp.data["results"]] == [str(soon.public_id)]
        resp = client.get("/api/v1/operator/trips/", {"status": "cancelled"})
        assert resp.data["count"] == 0


class TestPublicSearchAndHoldFixes:
    def test_date_search_finds_trip(self):
        """Regression: departure_at__date compiled to CONVERT_TZ(), which is
        NULL on a MySQL/MariaDB server without tz tables — date search
        silently returned nothing."""
        trip = make_trip(make_operator(), departure_in_hours=30)
        resp = client_for(make_staff(make_operator())).get(
            "/api/v1/trips/search/",
            {
                "origin": str(trip.route.origin_city.public_id),
                "destination": str(trip.route.destination_city.public_id),
                "date": timezone.localtime(trip.departure_at).date().isoformat(),
            },
        )
        assert resp.status_code == 200
        assert [row["public_id"] for row in resp.data["results"]] == [str(trip.public_id)]

    def test_search_keeps_delayed_trips(self):
        trip = make_trip(make_operator(), departure_in_hours=30, status=Trip.Status.DELAYED)
        resp = client_for(make_staff(make_operator())).get(
            "/api/v1/trips/search/", {"origin": str(trip.route.origin_city.public_id)}
        )
        assert [row["public_id"] for row in resp.data["results"]] == [str(trip.public_id)]

    def test_cannot_hold_seats_on_a_cancelled_trip(self):
        trip = make_trip(make_operator(), status=Trip.Status.CANCELLED)
        seat = trip.trip_seats.first()
        from rest_framework.test import APIClient

        resp = APIClient().post(
            f"/api/v1/trips/{trip.public_id}/hold/", {"seat_ids": [str(seat.seat.public_id)]}, format="json"
        )
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "trip_not_bookable"
        assert TripSeat.objects.get(pk=seat.pk).status == TripSeat.Status.AVAILABLE


class TestOperatorManifestAndBoarding:
    def test_manifest_lists_confirmed_and_pay_at_counter(self):
        operator = make_operator()
        trip = make_trip(operator, num_seats=4)
        paid_booking(trip, n=1)
        from apps.bookings import services as booking_services
        from apps.payments import services as payment_services

        seat_id = trip.trip_seats.filter(status="available").values_list("seat_id", flat=True).first()
        token = services.hold_seats(trip.id, [seat_id], session_key="counter-guest")
        pending = booking_services.create_booking(
            trip.id, token, [{"full_name": "Walk In"}], contact_phone="098765432"
        )
        payment_services.initiate_payment(pending, "counter")

        rows = client_for(make_staff(operator)).get(f"/api/v1/operator/trips/{trip.public_id}/manifest/").data
        statuses = sorted(row["payment_status"] for row in rows)
        assert statuses == ["paid", "pay_at_counter"]

    def test_bulk_mark_then_qr_scan_after_no_show(self):
        operator = make_operator()
        trip = make_trip(operator)
        booking = paid_booking(trip, n=2)
        passenger_ids = [str(p.public_id) for p in booking.passengers.all()]
        client = client_for(make_staff(operator))

        resp = client.post(
            f"/api/v1/operator/trips/{trip.public_id}/boarding/",
            {"passenger_ids": passenger_ids, "status": "no_show"},
            format="json",
        )
        assert resp.data == {"updated": 2}

        # A late arrival's QR scan must flip the no-show, not hit the
        # one-record-per-passenger unique constraint as "already boarded".
        from apps.boarding import services as boarding_services

        booking.refresh_from_db()
        records = boarding_services.validate_qr_and_board(booking.qr_token, trip.id)
        assert {r.status for r in records} == {"boarded"}
        rows = client.get(f"/api/v1/operator/trips/{trip.public_id}/manifest/").data
        assert all(row["boarded"] for row in rows)


class TestTripStatusAudit:
    def test_status_change_is_audit_logged_with_the_staff_actor(self):
        from apps.core.models import AuditLog

        operator = make_operator()
        trip = make_trip(operator)
        staff = make_staff(operator)
        client_for(staff).post(
            f"/api/v1/operator/trips/{trip.public_id}/status/",
            {"status": "cancelled", "cancellation_reason": "Engine failure"},
            format="json",
        )
        log = AuditLog.objects.get(action="trip_status_changed", target_id=str(trip.public_id))
        assert log.actor == staff
        assert log.before["status"] == "scheduled"
        assert log.after == {"status": "cancelled", "delay_minutes": None, "reason": "Engine failure"}
