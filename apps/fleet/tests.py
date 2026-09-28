import pytest
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.core.factories import client_for, make_bus, make_layout, make_operator, make_trip
from apps.fleet import services as fleet_services
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


def _make_operator_owner(phone):
    email = "user" + phone.removeprefix("+855") + "@bbms.test"
    user = User.objects.create_user(
        email=email, phone=phone, password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
    )
    operator = Operator.objects.create(
        name=f"Operator {phone}", contact_phone=phone, status=Operator.Status.APPROVED
    )
    OperatorStaff.objects.create(
        user=user, operator=operator, staff_role=OperatorStaff.StaffRole.OWNER, can_create_bookings=True
    )
    return user, operator


class TestSeatLayoutBuilder:
    def test_build_generates_seats_with_full_grid_data(self, api_client):
        owner, operator = _make_operator_owner("+85520000001")
        layout = SeatLayout.objects.create(
            operator=operator, name="Standard 40", bus_type=SeatLayout.BusType.SEATED
        )

        api_client.force_authenticate(owner)
        resp = api_client.post(
            f"/api/v1/seat-layouts/{layout.public_id}/build/",
            {"decks": 1, "rows": 10, "columns": ["A", "B", None, "C"]},
            format="json",
        )
        assert resp.status_code == 200
        assert len(resp.data) == 30  # 3 real seats/row * 10 rows, aisle skipped

        seat = resp.data[0]
        expected_keys = {"seat_number", "deck", "row_position", "col_position", "seat_type", "is_female_only"}
        assert set(seat.keys()) >= expected_keys

        layout.refresh_from_db()
        assert layout.seats.count() == 30
        assert layout.deck_count == 1

    def test_build_two_decks_prefixes_upper_deck_seat_numbers(self, api_client):
        owner, operator = _make_operator_owner("+85520000002")
        layout = SeatLayout.objects.create(
            operator=operator, name="Sleeper", bus_type=SeatLayout.BusType.SLEEPER
        )

        api_client.force_authenticate(owner)
        api_client.post(
            f"/api/v1/seat-layouts/{layout.public_id}/build/",
            {"decks": 2, "rows": 5, "columns": ["A", "B"]},
            format="json",
        )
        seat_numbers = set(Seat.objects.filter(seat_layout=layout).values_list("seat_number", flat=True))
        assert "A1" in seat_numbers
        assert "UA1" in seat_numbers

    def test_rebuild_replaces_previous_seats(self, api_client):
        owner, operator = _make_operator_owner("+85520000003")
        layout = SeatLayout.objects.create(
            operator=operator, name="Layout", bus_type=SeatLayout.BusType.SEATED
        )

        api_client.force_authenticate(owner)
        api_client.post(
            f"/api/v1/seat-layouts/{layout.public_id}/build/",
            {"decks": 1, "rows": 10, "columns": ["A", "B"]},
            format="json",
        )
        assert Seat.objects.filter(seat_layout=layout).count() == 20

        api_client.post(
            f"/api/v1/seat-layouts/{layout.public_id}/build/",
            {"decks": 1, "rows": 4, "columns": ["A"]},
            format="json",
        )
        assert Seat.objects.filter(seat_layout=layout).count() == 4


class TestFleetCrossOperatorAccess:
    def test_cannot_read_another_operators_bus(self, api_client):
        _owner_a, operator_a = _make_operator_owner("+85520000004")
        owner_b, operator_b = _make_operator_owner("+85520000005")
        layout_a = SeatLayout.objects.create(operator=operator_a, name="A-Layout")
        bus_a = Bus.objects.create(operator=operator_a, seat_layout=layout_a, plate_number="PP-1234")

        api_client.force_authenticate(owner_b)
        resp = api_client.get(f"/api/v1/buses/{bus_a.public_id}/")
        assert resp.status_code == 404

    def test_cannot_write_another_operators_bus(self, api_client):
        _owner_a, operator_a = _make_operator_owner("+85520000006")
        owner_b, operator_b = _make_operator_owner("+85520000007")
        layout_a = SeatLayout.objects.create(operator=operator_a, name="A-Layout")
        bus_a = Bus.objects.create(operator=operator_a, seat_layout=layout_a, plate_number="PP-5678")

        api_client.force_authenticate(owner_b)
        resp = api_client.patch(f"/api/v1/buses/{bus_a.public_id}/", {"plate_number": "STOLEN"})
        assert resp.status_code == 404
        bus_a.refresh_from_db()
        assert bus_a.plate_number == "PP-5678"

    def test_cannot_attach_bus_to_another_operators_seat_layout(self, api_client):
        _owner_a, operator_a = _make_operator_owner("+85520000008")
        owner_b, _operator_b = _make_operator_owner("+85520000009")
        layout_a = SeatLayout.objects.create(operator=operator_a, name="A-Layout")

        api_client.force_authenticate(owner_b)
        resp = api_client.post(
            "/api/v1/buses/",
            {"plate_number": "SR-0001", "seat_layout_id": str(layout_a.public_id)},
        )
        assert resp.status_code == 400

    def test_operator_only_sees_own_buses_in_list(self, api_client):
        owner_a, operator_a = _make_operator_owner("+85520000010")
        _owner_b, operator_b = _make_operator_owner("+85520000011")
        layout_a = SeatLayout.objects.create(operator=operator_a, name="A-Layout")
        layout_b = SeatLayout.objects.create(operator=operator_b, name="B-Layout")
        Bus.objects.create(operator=operator_a, seat_layout=layout_a, plate_number="PP-0001")
        Bus.objects.create(operator=operator_b, seat_layout=layout_b, plate_number="PP-0002")

        api_client.force_authenticate(owner_a)
        resp = api_client.get("/api/v1/buses/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1


class TestBusSeatLayoutReassignment:
    """
    Regression test for a real bug found via manual browser testing: a bus
    reassigned to a different seat_layout left its already-scheduled trips'
    TripSeats pointing at the *old* layout's Seat rows (generate_trip_seats
    only runs at trip-creation time), so every seat hold on those trips
    400'd with "Unknown seats" — HoldSeatsView scopes its seat lookup to
    trip.bus.seat_layout_id, which no longer matched. See apps/fleet/
    services.py's BusInUseError docstring for the full story.
    """

    def test_service_refuses_to_reassign_a_bus_with_an_active_trip(self):
        operator = make_operator()
        bus = make_bus(operator)
        make_trip(operator, bus=bus)
        other_layout, _ = make_layout(operator)

        with pytest.raises(fleet_services.BusInUseError):
            fleet_services.update_bus(bus, seat_layout=other_layout)

        bus.refresh_from_db()
        assert bus.seat_layout_id != other_layout.id

    def test_service_allows_reassigning_a_bus_with_no_active_trips(self):
        operator = make_operator()
        bus = make_bus(operator)
        other_layout, _ = make_layout(operator)

        fleet_services.update_bus(bus, seat_layout=other_layout)

        bus.refresh_from_db()
        assert bus.seat_layout_id == other_layout.id

    def test_endpoint_returns_409_instead_of_leaving_seats_unbookable(self):
        owner, operator = _make_operator_owner("+85520000099")
        bus = make_bus(operator)
        trip = make_trip(operator, bus=bus)
        other_layout, _ = make_layout(operator)

        client = client_for(owner)
        resp = client.patch(
            f"/api/v1/buses/{bus.public_id}/",
            {"seat_layout_id": str(other_layout.public_id)},
            format="json",
        )

        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "bus_in_use"
        # And the trip's seats are still genuinely holdable — this is the
        # actual user-facing guarantee, not just the error code.
        seat_id = trip.trip_seats.first().seat.public_id
        hold_resp = client.post(
            f"/api/v1/trips/{trip.public_id}/hold/", {"seat_ids": [str(seat_id)]}, format="json"
        )
        assert hold_resp.status_code == 201
