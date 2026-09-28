"""Step 15: currency-safe operator revenue, new groupings, dashboard."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.factories import client_for, make_bus, make_operator, make_staff, make_trip, paid_booking
from apps.fleet.models import Bus
from apps.payments.models import Payment
from apps.reports import services
from apps.trips.models import Trip

pytestmark = pytest.mark.django_db


class TestRevenueCurrencies:
    def test_khr_and_usd_are_never_summed_together(self):
        """Regression: before Step 15 a 41000 KHR payment and a 10.00 USD
        payment on one route came back as a single 41010.00 total."""
        operator = make_operator()
        trip = make_trip(operator)
        paid_booking(trip, currency="USD")
        paid_booking(trip, currency="KHR")

        rows = services.operator_revenue(operator, group_by="route")
        by_currency = {row["currency"]: row["total_amount"] for row in rows}
        assert by_currency == {"USD": Decimal("10.00"), "KHR": Decimal("41000.00")}

    def test_channel_grouping_and_commission(self):
        operator = make_operator(commission_rate="12.50")
        paid_booking(make_trip(operator))
        [row] = services.operator_revenue(operator, group_by="channel")
        assert row["channel"] == "web"
        assert row["commission_amount"] == Decimal("1.25")
        assert row["net_amount"] == Decimal("8.75")

    def test_day_grouping_uses_local_date_without_convert_tz(self):
        operator = make_operator()
        paid_booking(make_trip(operator), provider=Payment.Provider.ABA_PAYWAY)
        [row] = services.operator_revenue(operator, group_by="day")
        assert row["date"] == timezone.localdate()
        assert row["provider"] == "aba_payway"

    def test_local_date_is_correct_across_utc_midnight(self):
        operator = make_operator()
        booking = paid_booking(make_trip(operator))
        # 20:00 UTC is 03:00 next day in Phnom Penh (UTC+7).
        local_now = timezone.localtime()
        utc_evening = datetime(local_now.year, local_now.month, local_now.day, 20, 0, tzinfo=UTC) - timedelta(
            days=1
        )
        Payment.objects.filter(booking=booking).update(created_at=utc_evening)
        [row] = services.operator_revenue(operator, group_by="day")
        assert row["date"] == timezone.localtime(utc_evening).date()
        assert row["date"] != utc_evening.date()


class TestOperatorDashboard:
    def test_dashboard_kpis(self):
        operator = make_operator()
        now = timezone.now()
        today_trip = make_trip(operator, num_seats=4, departure_at=now + timedelta(minutes=90))
        paid_booking(today_trip, n=2, currency="USD")
        paid_booking(today_trip, n=1, currency="KHR")
        make_trip(
            operator, departure_at=now + timedelta(hours=3), status=Trip.Status.DELAYED, delay_minutes=15
        )
        bus = make_bus(operator)
        Bus.objects.filter(pk=bus.pk).update(insurance_expires_on=timezone.localdate() + timedelta(days=5))

        data = client_for(make_staff(operator)).get("/api/v1/reports/operator/dashboard/").data
        local_today = timezone.localdate()
        if timezone.localtime(now + timedelta(minutes=90)).date() == local_today:
            assert data["seats_sold_today"] == 3
            assert data["revenue_today"] == {"usd": "20.00", "khr": "41000.00"}
            # top_routes' 30-day window ends "today" (exclusive of tomorrow), so
            # this trip only counts in it when it departs before local midnight —
            # same day-boundary case as seats_sold_today/revenue_today above.
            assert data["top_routes"][0]["seats_sold"] == 3
        assert [t["public_id"] for t in data["departing_soon"]] == [str(today_trip.public_id)]
        assert len(data["delayed_trips"]) == 1
        assert data["attention"]["unassigned_trips"] == 2  # no driver/conductor yet
        assert data["attention"]["expiring_bus_documents"] == 1
        assert {p["currency"] for p in data["revenue_series"]} == {"USD", "KHR"}

    def test_counter_agent_cannot_see_dashboard(self):
        operator = make_operator()
        agent = make_staff(operator, staff_role="counter_agent")
        assert client_for(agent).get("/api/v1/reports/operator/dashboard/").status_code == 403
