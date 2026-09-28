import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.operators.models import Operator
from apps.routes.models import City, Route, RouteStop, Stop

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


def _make_operator_owner(phone):
    from apps.accounts.models import OperatorStaff

    email = "user" + phone.removeprefix("+855") + "@bbms.test"
    user = User.objects.create_user(
        email=email, phone=phone, password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
    )
    operator = Operator.objects.create(
        name=f"Operator {phone}", contact_phone=phone, status=Operator.Status.APPROVED
    )
    OperatorStaff.objects.create(
        user=user,
        operator=operator,
        staff_role=OperatorStaff.StaffRole.OWNER,
        can_create_bookings=True,
    )
    return user, operator


@pytest.fixture
def cities(db):
    phnom_penh = City.objects.create(name_en="Phnom Penh", name_km="ភ្នំពញ")
    siem_reap = City.objects.create(name_en="Siem Reap", name_km="សើមរាប")
    return phnom_penh, siem_reap


class TestPublicCityAndStopEndpoints:
    def test_anonymous_can_list_cities(self, api_client, cities):
        resp = api_client.get("/api/v1/cities/")
        assert resp.status_code == 200
        assert resp.data["count"] == 2

    def test_city_search(self, api_client, cities):
        resp = api_client.get("/api/v1/cities/?search=Siem")
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["name_en"] == "Siem Reap"

    def test_stops_filterable_by_city(self, api_client, cities):
        phnom_penh, siem_reap = cities
        Stop.objects.create(city=phnom_penh, name_en="Central Station", name_km="នន")
        Stop.objects.create(city=siem_reap, name_en="Airport Stop", name_km="នន")

        resp = api_client.get(f"/api/v1/stops/?city={phnom_penh.public_id}")
        assert resp.status_code == 200
        assert resp.data["count"] == 1
        assert resp.data["results"][0]["name_en"] == "Central Station"


class TestOperatorScopedRoutes:
    def test_cross_operator_read_is_blocked(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner_a, operator_a = _make_operator_owner("+85510000001")
        owner_b, operator_b = _make_operator_owner("+85510000002")

        route_b = Route.objects.create(
            operator=operator_b, origin_city=phnom_penh, destination_city=siem_reap
        )

        api_client.force_authenticate(owner_a)
        resp = api_client.get(f"/api/v1/routes/{route_b.public_id}/")
        assert resp.status_code == 404  # scoped queryset hides it entirely, not a 403

    def test_cross_operator_write_is_blocked(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner_a, operator_a = _make_operator_owner("+85510000003")
        owner_b, operator_b = _make_operator_owner("+85510000004")

        route_b = Route.objects.create(
            operator=operator_b, origin_city=phnom_penh, destination_city=siem_reap
        )

        api_client.force_authenticate(owner_a)
        resp = api_client.patch(f"/api/v1/routes/{route_b.public_id}/", {"name": "Hijacked"})
        assert resp.status_code == 404

        route_b.refresh_from_db()
        assert route_b.name != "Hijacked"

    def test_operator_only_sees_own_routes_in_list(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner_a, operator_a = _make_operator_owner("+85510000005")
        _owner_b, operator_b = _make_operator_owner("+85510000006")

        Route.objects.create(operator=operator_a, origin_city=phnom_penh, destination_city=siem_reap)
        Route.objects.create(operator=operator_b, origin_city=siem_reap, destination_city=phnom_penh)

        api_client.force_authenticate(owner_a)
        resp = api_client.get("/api/v1/routes/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_create_route_stamps_calling_operator(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner, operator = _make_operator_owner("+85510000007")
        api_client.force_authenticate(owner)

        resp = api_client.post(
            "/api/v1/routes/",
            {
                "origin_city_id": str(phnom_penh.public_id),
                "destination_city_id": str(siem_reap.public_id),
                "name": "PP-SR Express",
            },
        )
        assert resp.status_code == 201
        route = Route.objects.get(public_id=resp.data["public_id"])
        assert route.operator_id == operator.id


class TestRouteStops:
    def test_operator_can_add_and_list_own_route_stops(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner, operator = _make_operator_owner("+85510000008")
        route = Route.objects.create(operator=operator, origin_city=phnom_penh, destination_city=siem_reap)
        stop = Stop.objects.create(city=phnom_penh, name_en="Depot", name_km="នន")

        api_client.force_authenticate(owner)
        resp = api_client.post(
            f"/api/v1/routes/{route.public_id}/stops/",
            {"stop_id": str(stop.public_id), "sequence": 1, "offset_minutes": 0},
        )
        assert resp.status_code == 201

        resp = api_client.get(f"/api/v1/routes/{route.public_id}/stops/")
        assert resp.status_code == 200
        # Unpaginated: a route's whole stop list, never cut off at page_size.
        assert len(resp.data) == 1

    def test_duplicate_sequence_is_rejected(self, api_client, cities):
        phnom_penh, siem_reap = cities
        owner, operator = _make_operator_owner("+85510000009")
        route = Route.objects.create(operator=operator, origin_city=phnom_penh, destination_city=siem_reap)
        stop_a = Stop.objects.create(city=phnom_penh, name_en="Depot A", name_km="នន")
        stop_b = Stop.objects.create(city=phnom_penh, name_en="Depot B", name_km="នន")

        api_client.force_authenticate(owner)
        api_client.post(
            f"/api/v1/routes/{route.public_id}/stops/",
            {"stop_id": str(stop_a.public_id), "sequence": 1, "offset_minutes": 0},
        )
        resp = api_client.post(
            f"/api/v1/routes/{route.public_id}/stops/",
            {"stop_id": str(stop_b.public_id), "sequence": 1, "offset_minutes": 5},
        )
        assert resp.status_code == 400

    def test_cannot_add_stops_to_another_operators_route(self, api_client, cities):
        phnom_penh, siem_reap = cities
        _owner_a, operator_a = _make_operator_owner("+85510000010")
        owner_b, _operator_b = _make_operator_owner("+85510000011")
        route_a = Route.objects.create(
            operator=operator_a, origin_city=phnom_penh, destination_city=siem_reap
        )
        stop = Stop.objects.create(city=phnom_penh, name_en="Depot", name_km="នន")

        api_client.force_authenticate(owner_b)
        resp = api_client.post(
            f"/api/v1/routes/{route_a.public_id}/stops/",
            {"stop_id": str(stop.public_id), "sequence": 1, "offset_minutes": 0},
        )
        assert resp.status_code == 404
        assert not RouteStop.objects.filter(route=route_a).exists()
