"""Step 16: conductor offline support — manifest fingerprints, manual PNR
boarding, idempotent offline sync (no duplicate boarding records)."""

import hashlib
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import OperatorStaff
from apps.boarding.models import BoardingRecord
from apps.core.factories import client_for, make_operator, make_staff, make_trip, paid_booking

pytestmark = pytest.mark.django_db


def _conductor(operator):
    return make_staff(operator, staff_role=OperatorStaff.StaffRole.CONDUCTOR)


class TestConductorManifest:
    def test_rows_carry_qr_fingerprint_for_offline_validation(self):
        operator = make_operator()
        trip = make_trip(operator)
        booking = paid_booking(trip, n=2)
        booking.refresh_from_db()
        rows = client_for(_conductor(operator)).get(f"/api/v1/boarding/trips/{trip.public_id}/manifest/").data
        assert len(rows) == 2
        expected = hashlib.sha256(booking.qr_token.encode()).hexdigest()
        assert {row["qr_token_sha256"] for row in rows} == {expected}
        assert rows[0]["boarding_status"] == ""
        assert rows[0]["scanned_at"] is None


class TestBoardByPnr:
    def test_manual_pnr_boards_every_passenger(self):
        operator = make_operator()
        trip = make_trip(operator)
        booking = paid_booking(trip, n=2)
        resp = client_for(_conductor(operator)).post(
            f"/api/v1/boarding/trips/{trip.public_id}/board-pnr/", {"pnr": booking.pnr.lower()}, format="json"
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["pnr"] == booking.pnr
        assert BoardingRecord.objects.filter(booking=booking, status="boarded").count() == 2

    def test_rescan_reports_first_scan_time(self):
        operator = make_operator()
        trip = make_trip(operator)
        booking = paid_booking(trip)
        client = client_for(_conductor(operator))
        url = f"/api/v1/boarding/trips/{trip.public_id}/board-pnr/"
        client.post(url, {"pnr": booking.pnr}, format="json")
        resp = client.post(url, {"pnr": booking.pnr}, format="json")
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "already_boarded"
        assert "first_scanned_at" in resp.data["error"]["details"]

    def test_other_operators_pnr_is_not_found(self):
        trip_a = make_trip(make_operator())
        booking = paid_booking(trip_a)
        operator_b = make_operator()
        trip_b = make_trip(operator_b)
        resp = client_for(_conductor(operator_b)).post(
            f"/api/v1/boarding/trips/{trip_b.public_id}/board-pnr/", {"pnr": booking.pnr}, format="json"
        )
        assert resp.status_code == 404

    def test_wrong_trip_same_operator(self):
        operator = make_operator()
        trip_a, trip_b = make_trip(operator), make_trip(operator, departure_in_hours=100)
        booking = paid_booking(trip_a)
        resp = client_for(_conductor(operator)).post(
            f"/api/v1/boarding/trips/{trip_b.public_id}/board-pnr/", {"pnr": booking.pnr}, format="json"
        )
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "wrong_trip"


class TestOfflineSync:
    def _scan(self, passenger, status="boarded", scanned_at=None):
        return {
            "client_scan_id": str(uuid.uuid4()),
            "passenger_id": str(passenger.public_id),
            "status": status,
            "scanned_at": (scanned_at or timezone.now() - timedelta(minutes=5)).isoformat(),
        }

    def test_retried_sync_never_duplicates(self):
        """Done-when: queued offline scans sync without duplicating."""
        operator = make_operator()
        trip = make_trip(operator)
        booking = paid_booking(trip, n=2)
        scans = [self._scan(p) for p in booking.passengers.all()]
        client = client_for(_conductor(operator))
        url = f"/api/v1/boarding/trips/{trip.public_id}/sync/"

        first = client.post(url, {"scans": scans}, format="json")
        assert [r["result"] for r in first.data] == ["applied", "applied"]
        # The response was "lost" — the app retries the very same queue.
        second = client.post(url, {"scans": scans}, format="json")
        assert [r["result"] for r in second.data] == ["duplicate", "duplicate"]
        assert BoardingRecord.objects.filter(booking=booking).count() == 2

    def test_two_devices_boarding_the_same_passenger(self):
        operator = make_operator()
        trip = make_trip(operator)
        passenger = paid_booking(trip).passengers.get()
        url = f"/api/v1/boarding/trips/{trip.public_id}/sync/"
        client_for(_conductor(operator)).post(url, {"scans": [self._scan(passenger)]}, format="json")
        other = client_for(_conductor(operator)).post(url, {"scans": [self._scan(passenger)]}, format="json")
        assert other.data[0]["result"] == "duplicate"
        assert BoardingRecord.objects.filter(passenger=passenger).count() == 1

    def test_boarded_wins_over_no_show(self):
        operator = make_operator()
        trip = make_trip(operator)
        passenger = paid_booking(trip).passengers.get()
        client = client_for(_conductor(operator))
        url = f"/api/v1/boarding/trips/{trip.public_id}/sync/"

        assert client.post(url, {"scans": [self._scan(passenger, "no_show")]}, format="json").data[0][
            "result"
        ] == ("applied")
        assert client.post(url, {"scans": [self._scan(passenger, "boarded")]}, format="json").data[0][
            "result"
        ] == ("applied")
        late = client.post(url, {"scans": [self._scan(passenger, "no_show")]}, format="json")
        assert late.data[0]["result"] == "superseded"
        assert BoardingRecord.objects.get(passenger=passenger).status == "boarded"

    def test_rejects_passengers_not_on_this_trip_and_clamps_future_times(self):
        operator = make_operator()
        trip, other_trip = make_trip(operator), make_trip(operator, departure_in_hours=100)
        stranger = paid_booking(other_trip).passengers.get()
        mine = paid_booking(trip).passengers.get()
        resp = client_for(_conductor(operator)).post(
            f"/api/v1/boarding/trips/{trip.public_id}/sync/",
            {
                "scans": [
                    self._scan(stranger),
                    self._scan(mine, scanned_at=timezone.now() + timedelta(days=3)),  # skewed device clock
                ]
            },
            format="json",
        )
        assert resp.data[0] == {**resp.data[0], "result": "rejected", "reason": "not_on_trip"}
        assert resp.data[1]["result"] == "applied"
        assert BoardingRecord.objects.get(passenger=mine).scanned_at <= timezone.now()

    def test_non_conductor_cannot_sync(self):
        operator = make_operator()
        trip = make_trip(operator)
        agent = make_staff(operator, staff_role=OperatorStaff.StaffRole.COUNTER_AGENT)
        resp = client_for(agent).post(
            f"/api/v1/boarding/trips/{trip.public_id}/sync/",
            {
                "scans": [
                    {
                        "client_scan_id": str(uuid.uuid4()),
                        "passenger_id": str(uuid.uuid4()),
                        "status": "boarded",
                    }
                ]
            },
            format="json",
        )
        assert resp.status_code == 403


class TestConductorTrips:
    def test_mine_filter_lists_assigned_trips(self):
        operator = make_operator()
        conductor = _conductor(operator)
        assigned = make_trip(operator, conductor=conductor.operator_staff)
        make_trip(operator, departure_in_hours=100)
        resp = client_for(conductor).get("/api/v1/operator/trips/", {"mine": "true"})
        assert [row["public_id"] for row in resp.data["results"]] == [str(assigned.public_id)]
