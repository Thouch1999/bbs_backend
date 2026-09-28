"""Step 15: seat layout builder save, in-use guards, bus validation."""

import pytest

from apps.core.factories import client_for, make_bus, make_layout, make_operator, make_staff, make_trip
from apps.fleet.models import Seat
from apps.trips.serializers import TripSeatSerializer

pytestmark = pytest.mark.django_db


def _builder_payload():
    # 2 rows x [A, B, aisle, C]; B2 removed, C1 VIP, A2 female-only.
    return {
        "deck_count": 1,
        "seats": [
            {"seat_number": "a1", "row_position": 1, "col_position": 1},
            {"seat_number": "B1", "row_position": 1, "col_position": 2},
            {"seat_number": "C1", "row_position": 1, "col_position": 4, "seat_type": "vip"},
            {"seat_number": "A2", "row_position": 2, "col_position": 1, "is_female_only": True},
            {"seat_number": "C2", "row_position": 2, "col_position": 4},
        ],
    }


class TestSeatLayoutBuilderSave:
    def test_saves_explicit_seats_verbatim(self):
        operator = make_operator()
        layout, _ = make_layout(operator)
        resp = client_for(make_staff(operator)).put(
            f"/api/v1/seat-layouts/{layout.public_id}/seats/", _builder_payload(), format="json"
        )
        assert resp.status_code == 200, resp.data
        seats = {s["seat_number"]: s for s in resp.data["seats"]}
        assert set(seats) == {"A1", "B1", "C1", "A2", "C2"}  # normalized to upper case
        assert seats["C1"]["col_position"] == 4 and seats["C1"]["seat_type"] == "vip"
        assert seats["A2"]["is_female_only"] is True
        assert resp.data["in_use"] is False

    def test_passenger_seat_map_renders_the_saved_layout_unchanged(self):
        """Done-when: the builder writes a layout the passenger seat map can
        render unchanged — the trip seat map exposes exactly the positions
        and flags the builder saved (SeatMap.tsx places cells by
        row_position/col_position; the empty column 3 is the aisle)."""
        operator = make_operator()
        layout, _ = make_layout(operator)
        client_for(make_staff(operator)).put(
            f"/api/v1/seat-layouts/{layout.public_id}/seats/", _builder_payload(), format="json"
        )
        trip = make_trip(operator, bus=make_bus(operator, layout=layout))
        rendered = TripSeatSerializer(trip.trip_seats.select_related("seat"), many=True).data
        cells = {
            (
                r["seat"]["seat_number"],
                r["seat"]["row_position"],
                r["seat"]["col_position"],
                r["seat"]["is_female_only"],
            )
            for r in rendered
        }
        assert cells == {
            ("A1", 1, 1, False),
            ("B1", 1, 2, False),
            ("C1", 1, 4, False),
            ("A2", 2, 1, True),
            ("C2", 2, 4, False),
        }

    @pytest.mark.parametrize(
        "mutate, message",
        [
            (
                lambda p: p["seats"].append({"seat_number": "A1", "row_position": 3, "col_position": 1}),
                "twice",
            ),
            (
                lambda p: p["seats"].append({"seat_number": "Z9", "row_position": 1, "col_position": 1}),
                "share",
            ),
            (
                lambda p: p["seats"].append(
                    {"seat_number": "U1", "deck": 2, "row_position": 1, "col_position": 1}
                ),
                "deck",
            ),
        ],
    )
    def test_rejects_invalid_layouts(self, mutate, message):
        operator = make_operator()
        layout, _ = make_layout(operator)
        payload = _builder_payload()
        mutate(payload)
        resp = client_for(make_staff(operator)).put(
            f"/api/v1/seat-layouts/{layout.public_id}/seats/", payload, format="json"
        )
        assert resp.status_code == 400
        assert message in resp.data["error"]["message"]

    def test_layout_used_by_trips_is_read_only(self):
        """Regression: rewriting (or rebuilding) a layout that trips were
        generated from 500'd on the PROTECTed TripSeat -> Seat FK."""
        operator = make_operator()
        trip = make_trip(operator)
        layout = trip.bus.seat_layout
        client = client_for(make_staff(operator))

        resp = client.put(
            f"/api/v1/seat-layouts/{layout.public_id}/seats/", _builder_payload(), format="json"
        )
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "seat_layout_in_use"
        resp = client.post(
            f"/api/v1/seat-layouts/{layout.public_id}/build/",
            {"rows": 2, "columns": ["A", "B"]},
            format="json",
        )
        assert resp.status_code == 409
        assert Seat.objects.filter(seat_layout=layout).count() == 4
        assert client.get(f"/api/v1/seat-layouts/{layout.public_id}/").data["in_use"] is True


class TestBusValidation:
    def test_unknown_amenity_rejected(self):
        operator = make_operator()
        layout, _ = make_layout(operator)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/buses/",
            {
                "plate_number": "2A-1234",
                "seat_layout_id": str(layout.public_id),
                "amenities": ["ac", "jacuzzi"],
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_duplicate_plate_is_400_not_500(self):
        operator = make_operator()
        bus = make_bus(operator)
        resp = client_for(make_staff(operator)).post(
            "/api/v1/buses/",
            {"plate_number": bus.plate_number.lower(), "seat_layout_id": str(bus.seat_layout.public_id)},
            format="json",
        )
        assert resp.status_code == 400
        assert "plate_number" in resp.data["error"]["details"]

    def test_expiry_dates_round_trip(self):
        operator = make_operator()
        bus = make_bus(operator)
        resp = client_for(make_staff(operator)).patch(
            f"/api/v1/buses/{bus.public_id}/",
            {"registration_expires_on": "2027-03-01", "insurance_expires_on": "2026-12-31"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["insurance_expires_on"] == "2026-12-31"
