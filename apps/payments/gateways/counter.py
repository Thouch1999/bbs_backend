"""
Pay-at-counter (FR4.3) as a gateway, so payments/services.py keeps its
"never branch on the provider" shape. There's no external API: money moves
as cash across the operator's counter.

Before Step 16 "counter" had no entry in the gateway registry, so
create_refund() on a cash payment raised ValueError (a 500) — a cash
booking could never be refunded.
"""

import uuid
from decimal import Decimal

from .base import GatewayCallbackResult, GatewayRefundResult, InvalidSignatureError, PaymentGateway


class CounterGateway(PaymentGateway):
    provider_name = "counter"

    def initiate(self, payment) -> dict:
        # services.initiate_payment handles counter itself (it extends the
        # seat hold to the counter deadline); nothing to call out to.
        return {"pay_at_counter": True}

    def verify_callback(self, request) -> GatewayCallbackResult:
        raise InvalidSignatureError("Pay-at-counter has no webhooks; staff mark payments received.")

    def query_status(self, provider_txn_id: str) -> str:
        return "unknown"

    def refund(self, payment, amount: Decimal) -> GatewayRefundResult:
        """Recorded as a cash handback: the operator's counter returns the
        money in person, so it succeeds immediately."""
        return GatewayRefundResult(
            success=True,
            provider_refund_id=f"cash-refund-{uuid.uuid4().hex[:12]}",
            raw={"method": "cash", "amount": str(amount)},
        )
