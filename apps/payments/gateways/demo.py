"""
Presentation-only gateway: apps.payments.services.initiate_payment()
auto-confirms it immediately (see the Provider.DEMO branch there), so no
webhook ever actually arrives for it in real use. This class still
implements the full PaymentGateway interface (verify_callback/query_status/
refund) so it behaves like any other gateway if one is ever called directly
— e.g. in a test, or a refund on an already-confirmed demo payment.
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal

from django.conf import settings

from .base import GatewayCallbackResult, GatewayRefundResult, InvalidSignatureError, PaymentGateway


class DemoGateway(PaymentGateway):
    provider_name = "demo"

    def initiate(self, payment) -> dict:
        return {"demo": True, "merchant_ref": str(payment.public_id)}

    def verify_callback(self, request) -> GatewayCallbackResult:
        signature = request.headers.get("X-Demo-Signature", "")
        expected = hmac.new(settings.MOCK_GATEWAY_SECRET.encode(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidSignatureError("X-Demo-Signature did not match the computed HMAC.")

        payload = json.loads(request.body)
        return GatewayCallbackResult(
            merchant_ref=payload["merchant_ref"],
            provider_txn_id=payload.get("provider_txn_id") or f"demo-{uuid.uuid4().hex[:12]}",
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
            provider_refund_id=f"demo-refund-{uuid.uuid4().hex[:12]}",
            raw={"amount": str(amount)},
        )
