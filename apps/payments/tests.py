import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OperatorStaff, User
from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat

from . import services
from .gateways.mock import sign_mock_payload
from .models import Payment, PromoCode

pytestmark = pytest.mark.django_db

_plate_counter = itertools.count(1)


def _make_trip(num_seats=10, departure_in_hours=72, operator=None):
    operator = operator or Operator.objects.create(
        name="Test Operator", contact_phone="+85511111111", status="approved"
    )
    origin = City.objects.create(name_en="Phnom Penh", name_km="A")
    destination = City.objects.create(name_en="Siem Reap", name_km="B")
    route = Route.objects.create(operator=operator, origin_city=origin, destination_city=destination)
    layout = SeatLayout.objects.create(operator=operator, name="Layout")
    Seat.objects.bulk_create(
        [
            Seat(seat_layout=layout, seat_number=f"A{i}", deck=1, row_position=i, col_position=1)
            for i in range(1, num_seats + 1)
        ]
    )
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there) — refetch so seats[i].id is always usable.
    seats = list(Seat.objects.filter(seat_layout=layout).order_by("id"))
    bus = Bus.objects.create(
        operator=operator, seat_layout=layout, plate_number=f"PP-{next(_plate_counter):04d}"
    )
    departure_at = timezone.now() + timedelta(hours=departure_in_hours)
    trip = Trip.objects.create(
        route=route,
        bus=bus,
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=6),
        base_fare_usd="10.00",
        base_fare_khr="41000",
    )
    trip_services.generate_trip_seats(trip)
    return trip, seats, operator


def _passenger(name="Sok Dara"):
    return {"full_name": name, "age": 30, "gender": "male", "phone": "+85512345678"}


def _make_booking(trip, seats, n=1, **kwargs):
    seat_ids = [s.id for s in seats[:n]]
    token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest-session")
    passengers = [_passenger(f"Passenger {i}") for i in range(n)]
    defaults = {"contact_phone": "012345678"}
    defaults.update(kwargs)
    return booking_services.create_booking(trip.id, token, passengers, **defaults)


def _mock_request(payload: dict, *, bad_signature=False):
    body, signature = sign_mock_payload(payload)
    if bad_signature:
        signature = "0" * len(signature)
    request = RequestFactory().post(
        "/api/v1/payments/webhook/mock/", data=body, content_type="application/json"
    )
    request.META["HTTP_X_MOCK_SIGNATURE"] = signature
    return request


class TestInitiatePayment:
    def test_mock_gateway_initiate_returns_redirect_payload(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, gateway_payload = services.initiate_payment(booking, Payment.Provider.MOCK)
        assert payment.status == Payment.Status.PENDING
        assert "redirect_url" in gateway_payload

    def test_counter_payment_extends_the_hold(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, gateway_payload = services.initiate_payment(booking, Payment.Provider.COUNTER)

        assert payment.provider == Payment.Provider.COUNTER
        assert gateway_payload["pay_at_counter"] is True

        trip_seat = TripSeat.objects.get(booking_passenger__booking=booking)
        assert trip_seat.held_until > timezone.now() + timedelta(hours=1)

    def test_cannot_initiate_payment_twice_for_a_confirmed_booking(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        booking_services.confirm_booking(booking)
        with pytest.raises(services.PaymentStateError):
            services.initiate_payment(booking, Payment.Provider.MOCK)

    def test_real_gateway_is_not_implemented(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        from .gateways import GatewayNotImplementedError

        with pytest.raises(GatewayNotImplementedError):
            services.initiate_payment(booking, Payment.Provider.ABA_PAYWAY)


def _make_passenger(email="passenger@bbms.test"):
    return User.objects.create_user(email=email, password="x", role=User.Role.PASSENGER)


class TestInitiatePaymentEndpoint:
    """API-level coverage of InitiatePaymentView — TestInitiatePayment above
    only exercises services.initiate_payment() directly."""

    def test_endpoint_requires_login(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        resp = APIClient().post(
            "/api/v1/payments/",
            {"booking_id": str(booking.public_id), "provider": Payment.Provider.MOCK},
            format="json",
        )
        assert resp.status_code == 401

    def test_endpoint_returns_gateway_payload(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/payments/",
            {"booking_id": str(booking.public_id), "provider": Payment.Provider.MOCK},
            format="json",
        )
        assert resp.status_code == 201
        assert "redirect_url" in resp.data["gateway"]

    def test_endpoint_rejects_a_second_payment_for_a_confirmed_booking(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        booking_services.confirm_booking(booking)
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/payments/",
            {"booking_id": str(booking.public_id), "provider": Payment.Provider.MOCK},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "payment_state_error"

    def test_endpoint_returns_501_for_an_unwired_real_gateway(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/payments/",
            {"booking_id": str(booking.public_id), "provider": Payment.Provider.ABA_PAYWAY},
            format="json",
        )
        assert resp.status_code == 501

    def test_endpoint_auto_confirms_the_demo_provider(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/payments/",
            {"booking_id": str(booking.public_id), "provider": Payment.Provider.DEMO},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["payment"]["status"] == Payment.Status.SUCCEEDED
        booking.refresh_from_db()
        assert booking.status == Booking.Status.CONFIRMED


class TestWebhookIdempotencyAndSignature:
    def test_successful_webhook_confirms_booking_exactly_once(self, monkeypatch):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)

        calls = []
        original_confirm = booking_services.confirm_booking

        def counting_confirm(b):
            calls.append(b.id)
            return original_confirm(b)

        monkeypatch.setattr(booking_services, "confirm_booking", counting_confirm)

        payload = {
            "merchant_ref": str(payment.public_id),
            "provider_txn_id": "mock-txn-1",
            "status": "succeeded",
            "amount": "10.00",
            "currency": "USD",
        }
        request = _mock_request(payload)
        services.process_webhook("mock", request)

        booking.refresh_from_db()
        payment.refresh_from_db()
        assert booking.status == Booking.Status.CONFIRMED
        assert payment.status == Payment.Status.SUCCEEDED
        assert len(calls) == 1

    def test_replaying_the_same_webhook_is_a_no_op(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)

        payload = {
            "merchant_ref": str(payment.public_id),
            "provider_txn_id": "mock-txn-2",
            "status": "succeeded",
            "amount": "10.00",
            "currency": "USD",
        }

        services.process_webhook("mock", _mock_request(payload))
        first_updated_at = Payment.objects.get(pk=payment.pk).updated_at

        # Replay: must not re-run confirm_booking (which would raise
        # BookingStateError the second time if actually re-invoked) and
        # must not change the payment row.
        result = services.process_webhook("mock", _mock_request(payload))
        payment.refresh_from_db()
        booking.refresh_from_db()

        assert result.status == Payment.Status.SUCCEEDED
        assert payment.updated_at == first_updated_at
        assert booking.status == Booking.Status.CONFIRMED

    def test_invalid_signature_is_rejected_and_logged(self):
        from .gateways.base import InvalidSignatureError
        from .models import WebhookLog

        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)

        payload = {
            "merchant_ref": str(payment.public_id),
            "provider_txn_id": "mock-txn-3",
            "status": "succeeded",
        }
        request = _mock_request(payload, bad_signature=True)

        with pytest.raises(InvalidSignatureError):
            services.process_webhook("mock", request)

        payment.refresh_from_db()
        assert payment.status == Payment.Status.PENDING

        log = WebhookLog.objects.filter(provider="mock").latest("created_at")
        assert log.signature_valid is False

    def test_failed_payment_releases_seats_and_cancels_booking(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)

        payload = {
            "merchant_ref": str(payment.public_id),
            "provider_txn_id": "mock-txn-4",
            "status": "failed",
        }
        services.process_webhook("mock", _mock_request(payload))

        booking.refresh_from_db()
        trip.refresh_from_db()
        assert booking.status == Booking.Status.CANCELLED
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.AVAILABLE
        assert trip.seats_available == len(seats)

    def test_webhook_with_no_matching_payment_is_logged_and_ignored(self):
        from .models import WebhookLog

        payload = {"merchant_ref": "00000000-0000-0000-0000-000000000000", "status": "succeeded"}
        result = services.process_webhook("mock", _mock_request(payload))
        assert result is None
        log = WebhookLog.objects.filter(provider="mock").latest("created_at")
        assert log.signature_valid is True
        assert log.payment is None


class TestWebhookEndpoint:
    """API-level coverage of WebhookView — the tests above call
    services.process_webhook() directly, bypassing the view's own
    signature/unknown-provider branches and response shape."""

    def test_endpoint_confirms_booking_and_reports_the_payment_id(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payload = {
            "merchant_ref": str(payment.public_id),
            "provider_txn_id": "mock-txn-http-1",
            "status": "succeeded",
            "amount": "10.00",
            "currency": "USD",
        }
        body, signature = sign_mock_payload(payload)
        resp = APIClient().post(
            "/api/v1/payments/webhook/mock/",
            data=body,
            content_type="application/json",
            HTTP_X_MOCK_SIGNATURE=signature,
        )
        assert resp.status_code == 200
        assert resp.data == {"received": True, "payment": str(payment.public_id)}

    def test_endpoint_rejects_a_bad_signature(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payload = {"merchant_ref": str(payment.public_id), "status": "succeeded"}
        body, _signature = sign_mock_payload(payload)
        resp = APIClient().post(
            "/api/v1/payments/webhook/mock/",
            data=body,
            content_type="application/json",
            HTTP_X_MOCK_SIGNATURE="0" * 64,
        )
        assert resp.status_code == 400

    def test_endpoint_404s_for_an_unknown_provider(self):
        resp = APIClient().post(
            "/api/v1/payments/webhook/not-a-real-gateway/",
            data="{}",
            content_type="application/json",
        )
        assert resp.status_code == 404


class TestCounterPayment:
    def _counter_agent(self, operator):
        user = User.objects.create_user(
            email="user19999999@bbms.test", password="Str0ngPassw0rd!", role=User.Role.OPERATOR_STAFF
        )
        OperatorStaff.objects.create(
            user=user,
            operator=operator,
            staff_role=OperatorStaff.StaffRole.COUNTER_AGENT,
            can_create_bookings=True,
        )
        return user

    def test_staff_marks_counter_payment_paid(self):
        trip, seats, operator = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.COUNTER)
        agent = self._counter_agent(operator)

        client = APIClient()
        client.force_authenticate(agent)
        resp = client.post(f"/api/v1/payments/{payment.public_id}/mark-counter-paid/")

        assert resp.status_code == 200
        booking.refresh_from_db()
        assert booking.status == Booking.Status.CONFIRMED

    def test_cannot_mark_paid_twice(self):
        trip, seats, operator = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.COUNTER)
        services.mark_counter_payment_paid(payment)

        with pytest.raises(services.PaymentStateError):
            services.mark_counter_payment_paid(payment)

    def test_endpoint_rejects_marking_a_different_operators_payment_paid(self):
        trip, seats, operator = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.COUNTER)
        other_operator = Operator.objects.create(
            name="Other Operator", contact_phone="+85522222222", status="approved"
        )
        agent = self._counter_agent(other_operator)

        client = APIClient()
        client.force_authenticate(agent)
        resp = client.post(f"/api/v1/payments/{payment.public_id}/mark-counter-paid/")

        assert resp.status_code == 403
        assert resp.data["error"]["code"] == "cross_operator"

    def test_endpoint_rejects_marking_paid_twice(self):
        trip, seats, operator = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.COUNTER)
        agent = self._counter_agent(operator)
        client = APIClient()
        client.force_authenticate(agent)
        client.post(f"/api/v1/payments/{payment.public_id}/mark-counter-paid/")

        resp = client.post(f"/api/v1/payments/{payment.public_id}/mark-counter-paid/")

        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "payment_state_error"


class TestRefunds:
    def test_create_refund_via_mock_gateway(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])

        refund = services.create_refund(payment, Decimal("5.00"), reason="Partial refund")
        assert refund.status == "succeeded"
        assert refund.amount == Decimal("5.00")

    def test_cannot_refund_more_than_paid(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])

        with pytest.raises(services.PaymentStateError):
            services.create_refund(payment, Decimal("999.00"))

    def test_cannot_refund_a_pending_payment(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        with pytest.raises(services.PaymentStateError):
            services.create_refund(payment, Decimal("5.00"))


class TestCreateRefundEndpoint:
    def _admin(self):
        return User.objects.create_user(
            email="user13000000@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )

    def test_endpoint_issues_a_refund(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])

        client = APIClient()
        client.force_authenticate(self._admin())
        resp = client.post(
            f"/api/v1/payments/{payment.public_id}/refund/",
            {"amount": "5.00", "reason": "Bus broke down"},
            format="json",
        )

        assert resp.status_code == 201
        assert resp.data["amount"] == "5.00"
        assert resp.data["status"] == "succeeded"

    def test_endpoint_rejects_refunding_more_than_paid(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])

        client = APIClient()
        client.force_authenticate(self._admin())
        resp = client.post(
            f"/api/v1/payments/{payment.public_id}/refund/", {"amount": "999.00"}, format="json"
        )

        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "payment_state_error"

    def test_endpoint_returns_501_for_an_unwired_real_gateway(self):
        trip, seats, _op = _make_trip()
        booking = _make_booking(trip, seats)
        payment = Payment.objects.create(
            booking=booking,
            provider=Payment.Provider.ABA_PAYWAY,
            status=Payment.Status.SUCCEEDED,
            amount=booking.total_amount,
            currency=booking.currency,
        )

        client = APIClient()
        client.force_authenticate(self._admin())
        resp = client.post(f"/api/v1/payments/{payment.public_id}/refund/", {"amount": "1.00"}, format="json")

        assert resp.status_code == 501


class TestPromoCodeValidation:
    def _promo(self, **overrides):
        defaults = {
            "code": "SAVE10",
            "discount_type": PromoCode.DiscountType.PERCENT,
            "value": Decimal(10),
            "valid_from": timezone.now() - timedelta(days=1),
            "valid_until": timezone.now() + timedelta(days=1),
            "is_active": True,
        }
        defaults.update(overrides)
        return PromoCode.objects.create(**defaults)

    def test_percent_discount(self):
        self._promo()
        discount = services.validate_promo_code("SAVE10", subtotal=Decimal("100.00"))
        assert discount == Decimal("10.00")

    def test_fixed_discount_capped_at_subtotal(self):
        self._promo(code="FLAT50", discount_type=PromoCode.DiscountType.FIXED, value=Decimal("50.00"))
        discount = services.validate_promo_code("FLAT50", subtotal=Decimal("20.00"))
        assert discount == Decimal("20.00")

    def test_unknown_code_raises(self):
        with pytest.raises(services.PromoCodeNotFoundError):
            services.validate_promo_code("NOPE", subtotal=Decimal("100.00"))

    def test_expired_code_raises(self):
        self._promo(
            code="OLD10",
            valid_from=timezone.now() - timedelta(days=10),
            valid_until=timezone.now() - timedelta(days=5),
        )
        with pytest.raises(services.PromoCodeExpiredError):
            services.validate_promo_code("OLD10", subtotal=Decimal("100.00"))

    def test_not_yet_valid_code_raises(self):
        self._promo(
            code="FUTURE10",
            valid_from=timezone.now() + timedelta(days=5),
            valid_until=timezone.now() + timedelta(days=10),
        )
        with pytest.raises(services.PromoCodeExpiredError):
            services.validate_promo_code("FUTURE10", subtotal=Decimal("100.00"))

    def test_below_minimum_amount_raises(self):
        self._promo(code="MIN50", min_amount=Decimal("50.00"))
        with pytest.raises(services.PromoCodeMinAmountError):
            services.validate_promo_code("MIN50", subtotal=Decimal("10.00"))

    def test_wrong_operator_raises(self):
        trip, _seats, operator = _make_trip()
        other_operator = Operator.objects.create(
            name="Other Co", contact_phone="+85522222222", status="approved"
        )
        self._promo(code="OPONLY", applicable_operator=other_operator)
        with pytest.raises(services.PromoCodeNotApplicableError):
            services.validate_promo_code("OPONLY", subtotal=Decimal("100.00"), trip=trip)

    def test_matching_operator_succeeds(self):
        trip, _seats, operator = _make_trip()
        self._promo(code="OPONLY", applicable_operator=operator)
        discount = services.validate_promo_code("OPONLY", subtotal=Decimal("100.00"), trip=trip)
        assert discount == Decimal("10.00")

    def test_total_usage_limit_reached(self):
        trip, seats, _op = _make_trip()
        self._promo(code="LIMIT1", max_uses=1)
        booking = _make_booking(trip, seats, promo_code="LIMIT1")
        assert Booking.objects.filter(pk=booking.pk).exists()

        with pytest.raises(services.PromoCodeUsageLimitError):
            services.validate_promo_code("LIMIT1", subtotal=Decimal("100.00"))

    def test_cancelled_bookings_do_not_count_toward_usage(self):
        trip, seats, _op = _make_trip()
        self._promo(code="LIMIT1", max_uses=1)
        booking = _make_booking(trip, seats, promo_code="LIMIT1")
        booking_services.cancel_booking(booking)

        # Should not raise — the only usage was cancelled.
        services.validate_promo_code("LIMIT1", subtotal=Decimal("100.00"))

    def test_per_customer_usage_limit(self):
        trip, seats, _op = _make_trip(num_seats=4)
        self._promo(code="ONEEACH", max_uses_per_customer=1)
        _make_booking(trip, seats, n=1, promo_code="ONEEACH", contact_phone="012345678")

        with pytest.raises(services.PromoCodeUsageLimitError):
            services.validate_promo_code("ONEEACH", subtotal=Decimal("100.00"), contact_phone="+85512345678")

        # A different customer is unaffected.
        services.validate_promo_code("ONEEACH", subtotal=Decimal("100.00"), contact_phone="+85599999999")


class TestPromoValidateEndpoint:
    def test_endpoint_returns_discount(self):
        trip, _seats, _op = _make_trip()
        PromoCode.objects.create(
            code="SAVE10",
            discount_type=PromoCode.DiscountType.PERCENT,
            value=Decimal(10),
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=1),
        )
        client = APIClient()
        resp = client.post(
            "/api/v1/promo-codes/validate/",
            {"code": "SAVE10", "trip_id": str(trip.public_id), "num_seats": 2, "currency": "USD"},
        )
        assert resp.status_code == 200
        assert resp.data["subtotal"] == Decimal("20.00")
        assert resp.data["discount_amount"] == Decimal("2.00")

    def test_endpoint_rejects_invalid_code(self):
        trip, _seats, _op = _make_trip()
        client = APIClient()
        resp = client.post(
            "/api/v1/promo-codes/validate/",
            {"code": "NOPE", "trip_id": str(trip.public_id), "num_seats": 1, "currency": "USD"},
        )
        assert resp.status_code == 400


class TestCreateBookingWithPromoCode:
    def test_valid_promo_code_reduces_total(self):
        trip, seats, _op = _make_trip()
        PromoCode.objects.create(
            code="SAVE10",
            discount_type=PromoCode.DiscountType.PERCENT,
            value=Decimal(10),
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=1),
        )
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="s")
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/bookings/",
            {
                "trip_id": str(trip.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
                "currency": "USD",
                "promo_code": "SAVE10",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.data["discount_amount"] == "1.00"
        assert resp.data["total_amount"] == "9.00"

    def test_invalid_promo_code_rejects_booking(self):
        trip, seats, _op = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="s")
        client = APIClient()
        client.force_authenticate(_make_passenger())
        resp = client.post(
            "/api/v1/bookings/",
            {
                "trip_id": str(trip.public_id),
                "hold_token": token,
                "passengers": [_passenger()],
                "contact_phone": "012345678",
                "promo_code": "NOPE",
            },
            format="json",
        )
        assert resp.status_code == 400
        # the seat must not have been consumed by a half-created booking
        assert TripSeat.objects.get(trip=trip, seat=seats[0]).status == TripSeat.Status.HELD


class TestAdminPaymentListFilters:
    def _admin(self):
        return User.objects.create_user(
            email="user13111111@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )

    def test_filters_by_provider_currency_and_search(self):
        trip, seats, operator = _make_trip(num_seats=4)
        usd_booking = _make_booking(trip, seats[:1], n=1, currency="USD")
        payment_usd, _ = services.initiate_payment(usd_booking, Payment.Provider.MOCK)
        khr_booking = _make_booking(trip, seats[1:2], n=1, currency="KHR")
        payment_khr, _ = services.initiate_payment(khr_booking, Payment.Provider.COUNTER)

        client = APIClient()
        client.force_authenticate(self._admin())

        by_provider = client.get("/api/v1/admin/payments/", {"provider": "counter"}).data["results"]
        assert [r["public_id"] for r in by_provider] == [str(payment_khr.public_id)]

        by_currency = client.get("/api/v1/admin/payments/", {"currency": "KHR"}).data["results"]
        assert [r["public_id"] for r in by_currency] == [str(payment_khr.public_id)]

        by_search = client.get("/api/v1/admin/payments/", {"search": usd_booking.pnr}).data["results"]
        assert [r["public_id"] for r in by_search] == [str(payment_usd.public_id)]

        by_operator = client.get("/api/v1/admin/payments/", {"operator": str(operator.public_id)}).data[
            "results"
        ]
        assert len(by_operator) == 2

    def test_filters_by_status_and_date_range(self):
        trip, seats, _op = _make_trip(num_seats=4)
        booking = _make_booking(trip, seats[:1], n=1)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])
        booking_services.confirm_booking(booking)

        client = APIClient()
        client.force_authenticate(self._admin())

        paid = client.get("/api/v1/admin/payments/", {"status": "paid"}).data["results"]
        assert [r["public_id"] for r in paid] == [str(payment.public_id)]

        pending = client.get("/api/v1/admin/payments/", {"status": "pending"}).data["results"]
        assert pending == []

        today = timezone.localtime().date()
        in_range = client.get(
            "/api/v1/admin/payments/",
            {"date_from": str(today), "date_to": str(today)},
        ).data["results"]
        assert [r["public_id"] for r in in_range] == [str(payment.public_id)]

        tomorrow = today + timedelta(days=1)
        out_of_range = client.get("/api/v1/admin/payments/", {"date_from": str(tomorrow)}).data["results"]
        assert out_of_range == []


class TestAdminPayoutList:
    def test_list_returns_recorded_payouts(self):
        operator = Operator.objects.create(name="Payout Op", contact_phone="+85511122233", status="approved")
        admin = User.objects.create_user(
            email="user13222222@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )
        trip, seats, _op = _make_trip(num_seats=2, operator=operator)
        booking = _make_booking(trip, seats[:1], n=1)
        payment, _ = services.initiate_payment(booking, Payment.Provider.MOCK)
        payment.status = Payment.Status.SUCCEEDED
        payment.save(update_fields=["status"])
        booking_services.confirm_booking(booking)

        client = APIClient()
        client.force_authenticate(admin)
        payout = services.record_payout(
            operator,
            currency="USD",
            amount=Decimal("5.00"),
            period_start=timezone.now().date() - timedelta(days=1),
            period_end=timezone.now().date(),
            actor=admin,
        )

        resp = client.get("/api/v1/admin/payouts/")
        assert resp.status_code == 200
        assert [p["public_id"] for p in resp.data] == [str(payout.public_id)]


class TestAdminPromoCodeDestroyGuard:
    def _admin(self):
        return User.objects.create_user(
            email="user13333333@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )

    def test_cannot_delete_a_promo_code_already_used_on_a_booking(self):
        promo = PromoCode.objects.create(
            code="USEDCODE",
            discount_type=PromoCode.DiscountType.PERCENT,
            value=Decimal(10),
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=1),
        )
        trip, seats, _op = _make_trip()
        _make_booking(trip, seats, n=1, promo_code="USEDCODE")

        client = APIClient()
        client.force_authenticate(self._admin())
        resp = client.delete(f"/api/v1/admin/promo-codes/{promo.public_id}/")

        assert resp.status_code == 400
        assert PromoCode.objects.filter(pk=promo.pk).exists()

    def test_can_delete_an_unused_promo_code(self):
        promo = PromoCode.objects.create(
            code="UNUSEDCODE",
            discount_type=PromoCode.DiscountType.PERCENT,
            value=Decimal(10),
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=1),
        )
        client = APIClient()
        client.force_authenticate(self._admin())
        resp = client.delete(f"/api/v1/admin/promo-codes/{promo.public_id}/")

        assert resp.status_code == 204
        assert not PromoCode.objects.filter(pk=promo.pk).exists()
