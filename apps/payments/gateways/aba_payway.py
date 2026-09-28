"""
ABA PayWay integration — NOT implemented. This is scaffolding for whoever
wires up the real merchant integration, not a working gateway.

Everything marked TODO below requires ABA PayWay's actual merchant API
documentation (obtained once a merchant account exists) — endpoint URLs,
the exact request/response shape, and the signature algorithm are all
provider-specific and must come from their docs, not be guessed. Per
CLAUDE.md/SRS Step 8: never invent endpoint URLs or signature formats.
"""

from decimal import Decimal

from django.conf import settings

from .base import GatewayCallbackResult, GatewayNotImplementedError, GatewayRefundResult, PaymentGateway

_NOT_IMPLEMENTED = (
    "ABA PayWay is not wired up yet — the merchant API contract (purchase "
    "endpoint, callback payload shape, signature algorithm) must come from "
    "ABA's docs once a merchant account exists. Use MockGateway for now."
)


class ABAPayWayGateway(PaymentGateway):
    provider_name = "aba_payway"

    # TODO: confirm against ABA PayWay's merchant integration docs.
    API_BASE_URL = None  # e.g. "https://checkout-sandbox.payway.com.kh/api/payment-gateway/v1/payments/purchase"
    SIGNATURE_ALGORITHM = None  # ABA PayWay commonly documents HMAC-SHA512 — confirm before relying on this
    MERCHANT_ID = settings.ABA_PAYWAY_MERCHANT_ID
    API_KEY = settings.ABA_PAYWAY_API_KEY

    def initiate(self, payment) -> dict:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def verify_callback(self, request) -> GatewayCallbackResult:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def query_status(self, provider_txn_id: str) -> str:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def refund(self, payment, amount: Decimal) -> GatewayRefundResult:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)
