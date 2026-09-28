"""Step 15: whole-list route stop replace (drag-to-reorder)."""

import pytest

from apps.bookings.models import Booking
from apps.core.factories import client_for, make_operator, make_route, make_staff, make_trip, paid_booking
from apps.routes.models import RouteStop, Stop

pytestmark = pytest.mark.django_db


def _stops(route, n):
    return [Stop.objects.create(city=route.origin_city, name_en=f"Stop {i}", name_km="ច") for i in range(n)]


def _url(route):
    return f"/api/v1/routes/{route.public_id}/stops/bulk/"


class TestRouteStopListReplace:
    def test_reorder_keeps_existing_rows(self):
        operator = make_operator()
        route = make_route(operator)
        a, b, c = _stops(route, 3)
        rs_a = RouteStop.objects.create(route=route, stop=a, sequence=1, offset_minutes=0)
        rs_b = RouteStop.objects.create(route=route, stop=b, sequence=2, offset_minutes=60)
        client = client_for(make_staff(operator))

        resp = client.put(
            _url(route),
            {
                "stops": [
                    {"route_stop_id": str(rs_b.public_id), "stop_id": str(b.public_id), "offset_minutes": 0},
                    {"stop_id": str(c.public_id), "offset_minutes": 30},
                    {"route_stop_id": str(rs_a.public_id), "stop_id": str(a.public_id), "offset_minutes": 90},
                ]
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert [row["stop"]["name_en"] for row in resp.data] == ["Stop 1", "Stop 2", "Stop 0"]
        assert [row["sequence"] for row in resp.data] == [1, 2, 3]
        assert resp.data[0]["public_id"] == str(rs_b.public_id)  # same row, moved
        assert RouteStop.objects.filter(route=route).count() == 3

    def test_decreasing_offsets_rejected(self):
        operator = make_operator()
        route = make_route(operator)
        a, b = _stops(route, 2)
        resp = client_for(make_staff(operator)).put(
            _url(route),
            {
                "stops": [
                    {"stop_id": str(a.public_id), "offset_minutes": 60},
                    {"stop_id": str(b.public_id), "offset_minutes": 30},
                ]
            },
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "route_stops_error"

    def test_removing_a_stop_a_booking_uses_rolls_back(self):
        operator = make_operator()
        route = make_route(operator)
        a, b = _stops(route, 2)
        rs_a = RouteStop.objects.create(route=route, stop=a, sequence=1, offset_minutes=0)
        RouteStop.objects.create(route=route, stop=b, sequence=2, offset_minutes=60)
        booking = paid_booking(make_trip(operator, route=route))
        Booking.objects.filter(pk=booking.pk).update(boarding_stop=rs_a)
        client = client_for(make_staff(operator))
        client.raise_request_exception = False

        resp = client.put(
            _url(route), {"stops": [{"stop_id": str(b.public_id), "offset_minutes": 0}]}, format="json"
        )
        assert resp.status_code == 409
        assert list(
            RouteStop.objects.filter(route=route).order_by("sequence").values_list("sequence", flat=True)
        ) == [
            1,
            2,
        ]

    def test_other_operators_route_is_404(self):
        route = make_route(make_operator())
        (a,) = _stops(route, 1)
        resp = client_for(make_staff(make_operator())).put(
            _url(route), {"stops": [{"stop_id": str(a.public_id), "offset_minutes": 0}]}, format="json"
        )
        assert resp.status_code == 404
