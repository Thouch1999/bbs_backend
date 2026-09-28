"""Step 15: operator staff management + profile staff block + core fixes."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.core.factories import client_for, make_bus, make_operator, make_route, make_staff, make_trip
from apps.core.models import AuditLog
from apps.operators.models import Operator

pytestmark = pytest.mark.django_db

_NEW_AGENT = {
    "email": "user12555666@bbms.test",
    "full_name": "Chan Dara",
    "password": "Str0ngPassw0rd!",
    "staff_role": "counter_agent",
}


class TestStaffManagement:
    def test_owner_creates_counter_agent_with_role_defaults(self):
        operator = make_operator()
        owner = make_staff(operator)
        resp = client_for(owner).post("/api/v1/operator/staff/", _NEW_AGENT, format="json")

        assert resp.status_code == 201, resp.data
        assert resp.data["email"] == "user12555666@bbms.test"
        assert resp.data["can_create_bookings"] is True
        assert resp.data["can_validate_tickets"] is False
        user = User.objects.get(email="user12555666@bbms.test")
        assert user.role == User.Role.OPERATOR_STAFF
        assert user.check_password("Str0ngPassw0rd!")
        assert user.operator_staff.operator == operator
        assert AuditLog.objects.filter(action="role_changed", target_id=str(user.public_id)).exists()

    def test_conductor_toggle_overrides(self):
        operator = make_operator()
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/staff/",
            {**_NEW_AGENT, "staff_role": "conductor", "can_create_bookings": True},
            format="json",
        )
        assert resp.data["can_validate_tickets"] is True
        assert resp.data["can_create_bookings"] is True

    def test_existing_phone_is_refused_not_hijacked(self):
        passenger = User.objects.create_user(email="user12555666@bbms.test", password="x")
        operator = make_operator()
        resp = client_for(make_staff(operator)).post("/api/v1/operator/staff/", _NEW_AGENT, format="json")
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "email_already_registered"
        passenger.refresh_from_db()
        assert passenger.role == User.Role.PASSENGER

    def test_cannot_create_manager_or_owner(self):
        operator = make_operator()
        resp = client_for(make_staff(operator)).post(
            "/api/v1/operator/staff/", {**_NEW_AGENT, "staff_role": "manager"}, format="json"
        )
        assert resp.status_code == 400

    def test_update_toggles_and_deactivate(self):
        operator = make_operator()
        owner = make_staff(operator)
        agent = make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT)
        url = f"/api/v1/operator/staff/{agent.operator_staff.public_id}/"

        resp = client_for(owner).patch(url, {"can_validate_tickets": True, "is_active": False}, format="json")
        assert resp.status_code == 200
        assert resp.data["can_validate_tickets"] is True
        assert resp.data["is_active"] is False
        agent.refresh_from_db()
        assert agent.is_active is False

    def test_owner_row_is_protected(self):
        operator = make_operator()
        owner = make_staff(operator)
        manager = make_staff(operator, staff_role=OperatorStaff.StaffRole.MANAGER)
        resp = client_for(manager).patch(
            f"/api/v1/operator/staff/{owner.operator_staff.public_id}/", {"is_active": False}, format="json"
        )
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "protected_staff_member"

    def test_counter_agent_cannot_manage_staff(self):
        operator = make_operator()
        agent = make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT)
        assert client_for(agent).get("/api/v1/operator/staff/").status_code == 403

    def test_staff_list_is_operator_scoped(self):
        operator_a, operator_b = make_operator(), make_operator()
        make_staff(operator_b, staff_role=OperatorStaff.StaffRole.CONDUCTOR)
        resp = client_for(make_staff(operator_a)).get("/api/v1/operator/staff/")
        assert len(resp.data) == 1  # just the caller


class TestProfileStaffBlock:
    def test_staff_profile_carries_role_and_permissions(self):
        operator = make_operator()
        conductor = make_staff(operator, staff_role=OperatorStaff.StaffRole.CONDUCTOR)
        data = client_for(conductor).get("/api/v1/profile/").data
        assert data["operator_staff"]["staff_role"] == "conductor"
        assert data["operator_staff"]["can_validate_tickets"] is True
        assert data["operator_staff"]["operator_public_id"] == str(operator.public_id)

    def test_passenger_profile_has_null_staff_block(self):
        passenger = User.objects.create_user(email="user12000999@bbms.test", password="x")
        assert client_for(passenger).get("/api/v1/profile/").data["operator_staff"] is None


class TestCoreBackOfficeFixes:
    def test_deleting_a_bus_with_trips_is_409(self):
        operator = make_operator()
        trip = make_trip(operator)
        client = client_for(make_staff(operator))
        client.raise_request_exception = False
        resp = client.delete(f"/api/v1/buses/{trip.bus.public_id}/")
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "in_use"

    def test_pending_operator_can_read_but_not_write_fleet(self):
        operator = make_operator(status=Operator.Status.PENDING)
        make_bus(operator)
        client = client_for(make_staff(operator))
        assert client.get("/api/v1/buses/").status_code == 200
        route = make_route(make_operator())
        resp = client.post(
            "/api/v1/routes/",
            {
                "origin_city_id": str(route.origin_city.public_id),
                "destination_city_id": str(route.destination_city.public_id),
            },
            format="json",
        )
        assert resp.status_code == 403

    def test_public_config(self):
        resp = APIClient().get("/api/v1/config/")
        assert resp.status_code == 200
        assert resp.data["khr_per_usd"] == "4100.00"
        assert resp.data["seat_hold_minutes"] == 10
