"""Step 16: admin payments/reconciliation, payouts, refunds, promo admin."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.core.factories import client_for, make_operator, make_staff, make_trip, paid_booking
from apps.core.models import AuditLog
from apps.payments import services
from apps.payments.models import OperatorPayout, Payment, PromoCode

pytestmark = pytest.mark.django_db


def _admin():
    return User.objects.create_user(
        email="user17000001@bbms.test", password="x", role=User.Role.ADMIN, is_staff=True
    )


class TestRefundFixes:
    def test_cash_counter_payment_can_be_refunded(self):
        """Regression: "counter" had no gateway, so refunding a cash payment 500'd."""
        booking = paid_booking(make_trip(make_operator()), provider=Payment.Provider.COUNTER)
        payment = booking.payments.get()
        refund = services.create_refund(payment, Decimal("4.00"), reason="Changed plans")
        assert refund.status == "succeeded"
        assert refund.provider_refund_id.startswith("cash-refund-")

    def test_partial_refunds_cannot_exceed_the_payment(self):
        payment = paid_booking(make_trip(make_operator())).payments.get()  # 10.00
        services.create_refund(payment, Decimal("6.00"))
        with pytest.raises(services.PaymentStateError):
            services.create_refund(payment, Decimal("6.00"))
        assert services.refundable_balance(payment) == Decimal("4.00")


class TestPaymentMismatches:
    def test_amount_and_state_mismatches_are_flagged_and_filterable(self):
        operator = make_operator()
        trip = make_trip(operator, num_seats=6)
        ok = paid_booking(trip)
        wrong_amount = paid_booking(trip)
        Payment.objects.filter(booking=wrong_amount).update(amount=Decimal("7.00"))
        unconfirmed = paid_booking(trip)
        Booking.objects.filter(pk=unconfirmed.pk).update(status=Booking.Status.PENDING_PAYMENT)

        client = client_for(_admin())
        rows = {row["pnr"]: row for row in client.get("/api/v1/admin/payments/").data["results"]}
        assert rows[ok.pnr]["reconciliation_status"] == "paid"
        assert rows[wrong_amount.pnr]["flags"] == ["amount_mismatch"]
        assert rows[unconfirmed.pnr]["reconciliation_status"] == "mismatch"
        assert rows[ok.pnr]["commission"] == "1.00" and rows[ok.pnr]["net_to_operator"] == "9.00"

        mismatches = client.get("/api/v1/admin/payments/", {"status": "mismatch"}).data["results"]
        assert {row["pnr"] for row in mismatches} == {wrong_amount.pnr, unconfirmed.pnr}
        assert client.get("/api/v1/admin/reconciliation/summary/").data["mismatch_count"] == 2

    def test_rescheduled_booking_amount_change_is_not_a_mismatch(self):
        booking = paid_booking(make_trip(make_operator()))
        Booking.objects.filter(pk=booking.pk).update(
            total_amount=Decimal("16.00"), rescheduled_at=timezone.now()
        )
        payment = Payment.objects.select_related("booking").get(booking=booking)
        assert services.payment_flags(payment) == []

    def test_investigate_detail_compares_both_records(self):
        booking = paid_booking(make_trip(make_operator()))
        payment = booking.payments.get()
        data = client_for(_admin()).get(f"/api/v1/admin/payments/{payment.public_id}/").data
        assert data["booking_total_amount"] == "10.00" and data["amount"] == "10.00"
        assert data["refundable"] == "10.00"
        assert data["webhook_logs"] == []


class TestPayouts:
    def test_balances_are_per_currency_and_payouts_reduce_them(self):
        operator = make_operator(commission_rate="10.00")
        trip = make_trip(operator, num_seats=6)
        paid_booking(trip, currency="USD")  # 10.00
        paid_booking(trip, currency="KHR")  # 41000
        admin = _admin()
        client = client_for(admin)

        balances = {row["currency"]: row for row in client.get("/api/v1/admin/payouts/balances/").data}
        assert balances["USD"]["outstanding"] == "9.00"
        assert balances["KHR"]["outstanding"] == "36900.00"

        today = timezone.localdate().isoformat()
        body = {
            "operator_id": str(operator.public_id),
            "currency": "USD",
            "amount": "9.00",
            "period_start": today,
            "period_end": today,
            "reference": "ABA-123",
        }
        resp = client.post("/api/v1/admin/payouts/", body, format="json")
        assert resp.status_code == 201, resp.data
        balances = {row["currency"]: row for row in client.get("/api/v1/admin/payouts/balances/").data}
        assert balances["USD"]["outstanding"] == "0.00"
        assert balances["KHR"]["outstanding"] == "36900.00"
        assert AuditLog.objects.filter(action="payout_recorded", actor=admin).exists()

        over = client.post("/api/v1/admin/payouts/", {**body, "amount": "0.01"}, format="json")
        assert over.status_code == 400
        assert OperatorPayout.objects.count() == 1

    def test_refunds_reduce_what_is_owed(self):
        operator = make_operator(commission_rate="10.00")
        booking = paid_booking(make_trip(operator, departure_in_hours=100))
        booking_services.cancel_booking(booking)
        services.create_refund(booking.payments.get(), Decimal("9.00"))
        [row] = services.operator_balances()
        assert row["earned"] == Decimal("0.00")  # 10 gross - 9 refunded - 1 commission


class TestAdminPromoCodes:
    def _body(self, **extra):
        now = timezone.now()
        return {
            "code": "rainy20",
            "description_km": "បញ្ចុះ ២០%",
            "description_en": "20% off",
            "discount_type": "percent",
            "value": "20.00",
            "valid_from": now.isoformat(),
            "valid_until": (now + timedelta(days=30)).isoformat(),
            **extra,
        }

    def test_crud_with_usage_count(self):
        client = client_for(_admin())
        resp = client.post("/api/v1/admin/promo-codes/", self._body(), format="json")
        assert resp.status_code == 201, resp.data
        assert resp.data["code"] == "RAINY20"
        booking = paid_booking(make_trip(make_operator()))
        Booking.objects.filter(pk=booking.pk).update(promo_code="RAINY20")
        [row] = client.get("/api/v1/admin/promo-codes/").data["results"]
        assert row["times_used"] == 1
        client.raise_request_exception = False
        assert client.delete(f"/api/v1/admin/promo-codes/{row['public_id']}/").status_code == 400
        assert PromoCode.objects.filter(code="RAINY20").exists()

    def test_percentage_over_100_rejected(self):
        resp = client_for(_admin()).post("/api/v1/admin/promo-codes/", self._body(value="150"), format="json")
        assert resp.status_code == 400

    def test_operator_staff_cannot_manage_promos(self):
        staff = make_staff(make_operator())
        assert client_for(staff).get("/api/v1/admin/promo-codes/").status_code == 403
