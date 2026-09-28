"""
A fully-working gateway for local dev and tests, standing in for whichever
real provider isn't wired up yet. Its webhook signature scheme (HMAC-SHA256
over the raw body, hex-encoded, in an X-Mock-Signature header) is entirely
our own invention for this purpose — it is NOT modeled on any real
provider's actual scheme.
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

from django.conf import settings

from .base import GatewayCallbackResult, GatewayRefundResult, InvalidSignatureError, PaymentGateway


def sign_mock_payload(payload: dict) -> tuple[bytes, str]:
    """Test helper mirroring what a real MockGateway webhook call would send."""
    body = json.dumps(payload).encode()
    signature = hmac.new(settings.MOCK_GATEWAY_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


class MockGateway(PaymentGateway):
    provider_name = "mock"

    def initiate(self, payment) -> dict:
        return {
            "redirect_url": f"https://mock-gateway.test/pay/{payment.public_id}",
            "qr_payload": f"mock-qr:{payment.public_id}",
            "merchant_ref": str(payment.public_id),
        }

    def verify_callback(self, request) -> GatewayCallbackResult:
        signature = request.headers.get("X-Mock-Signature", "")
        expected = hmac.new(settings.MOCK_GATEWAY_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidSignatureError("X-Mock-Signature did not match the computed HMAC.")

        payload = json.loads(request.body)
        return GatewayCallbackResult(
            merchant_ref=payload["merchant_ref"],
            provider_txn_id=payload.get("provider_txn_id") or f"mock-{uuid.uuid4().hex[:12]}",
            succeeded=payload["status"] == "succeeded",
            raw=payload,
        )

    def query_status(self, provider_txn_id: str) -> str:
        from apps.payments.models import Payment

        payment = Payment.objects.filter(provider_txn_id=provider_txn_id).first()
        return payment.status if payment else "unknown"

    def refund(self, payment, amount: Decimal) -> GatewayRefundResult:
        return GatewayRefundResult(
            success=True,
            provider_refund_id=f"mock-refund-{uuid.uuid4().hex[:12]}",
            raw={"amount": str(amount)},
        )
