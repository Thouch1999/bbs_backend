"""Step 15: operator bookings list, operator-side cancel, customer lookup."""

import pytest

from apps.accounts.models import OperatorStaff
from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.core.factories import client_for, make_operator, make_staff, make_trip, paid_booking
from apps.core.models import AuditLog
from apps.payments import services as payment_services
from apps.trips import services as trip_services

pytestmark = pytest.mark.django_db


def _counter_booking(trip, phone="098765432"):
    seat_id = trip.trip_seats.filter(status="available").values_list("seat_id", flat=True).first()
    token = trip_services.hold_seats(trip.id, [seat_id], session_key="counter")
    booking = booking_services.create_booking(
        trip.id, token, [{"full_name": "Walk In", "age": 40, "gender": "female"}], contact_phone=phone
    )
    payment, _ = payment_services.initiate_payment(booking, "counter")
    return booking, payment


class TestOperatorBookingList:
    def test_scoped_to_operator_and_filterable(self):
        operator = make_operator()
        trip = make_trip(operator)
        paid = paid_booking(trip)
        pending, payment = _counter_booking(trip)
        paid_booking(make_trip(make_operator()))  # another operator's
        client = client_for(make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT))

        resp = client.get("/api/v1/operator/bookings/")
        assert resp.status_code == 200
        assert {row["pnr"] for row in resp.data["results"]} == {paid.pnr, pending.pnr}

        resp = client.get("/api/v1/operator/bookings/", {"pending_counter": "true"})
        [row] = resp.data["results"]
        assert row["pnr"] == pending.pnr
        assert row["pending_counter_payment_id"] == str(payment.public_id)
        assert row["route_name"] == trip.route.name

        resp = client.get("/api/v1/operator/bookings/", {"search": paid.pnr.lower()})
        assert [row["pnr"] for row in resp.data["results"]] == [paid.pnr]
        resp = client.get("/api/v1/operator/bookings/", {"search": "Walk"})
        assert [row["pnr"] for row in resp.data["results"]] == [pending.pnr]

    def test_conductor_without_booking_rights_is_forbidden(self):
        operator = make_operator()
        conductor = make_staff(operator, staff_role=OperatorStaff.StaffRole.CONDUCTOR)
        assert client_for(conductor).get("/api/v1/operator/bookings/").status_code == 403


class TestOperatorCancel:
    def test_preview_then_cancel_matches_and_audits_staff(self):
        operator = make_operator()
        booking = paid_booking(make_trip(operator, departure_in_hours=80))
        staff = make_staff(operator)
        client = client_for(staff)

        preview = client.get(f"/api/v1/operator/bookings/{booking.public_id}/cancellation-preview/").data
        resp = client.post(
            f"/api/v1/operator/bookings/{booking.public_id}/cancel/",
            {"reason": "Customer called"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"
        assert resp.data["refund_amount"] == preview["refund_amount"] == "9.00"
        log = AuditLog.objects.get(action="booking_cancelled", target_id=str(booking.public_id))
        assert log.actor == staff

    def test_cannot_cancel_other_operators_booking(self):
        booking = paid_booking(make_trip(make_operator()))
        resp = client_for(make_staff(make_operator())).post(
            f"/api/v1/operator/bookings/{booking.public_id}/cancel/", {}, format="json"
        )
        assert resp.status_code == 404
        booking.refresh_from_db()
        assert booking.status == Booking.Status.CONFIRMED


class TestCustomerLookup:
    def test_returns_latest_passengers_for_this_operator_only(self):
        operator = make_operator()
        _counter_booking(make_trip(operator), phone="098765432")
        client = client_for(make_staff(operator))

        resp = client.get("/api/v1/operator/customers/lookup/", {"phone": "098765432"})
        assert resp.status_code == 200
        assert resp.data["contact_phone"] == "+85598765432"
        assert resp.data["passengers"][0]["full_name"] == "Walk In"

        other = client_for(make_staff(make_operator()))
        assert other.get("/api/v1/operator/customers/lookup/", {"phone": "098765432"}).status_code == 404
