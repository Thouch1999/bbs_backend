"""
Wing integration — NOT implemented. See aba_payway.py's module docstring;
the same reasoning applies here. Do not invent Wing's endpoint URLs or
signature format — they must come from Wing's merchant API docs.
"""

from decimal import Decimal

from django.conf import settings

from .base import GatewayCallbackResult, GatewayNotImplementedError, GatewayRefundResult, PaymentGateway

_NOT_IMPLEMENTED = (
    "Wing is not wired up yet — the merchant API contract must come from "
    "Wing's docs once a merchant account exists. Use MockGateway for now."
)


class WingGateway(PaymentGateway):
    provider_name = "wing"

    # TODO: confirm against Wing's merchant integration docs.
    API_BASE_URL = None
    SIGNATURE_ALGORITHM = None
    MERCHANT_ID = settings.WING_MERCHANT_ID
    API_KEY = settings.WING_API_KEY

    def initiate(self, payment) -> dict:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def verify_callback(self, request) -> GatewayCallbackResult:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def query_status(self, provider_txn_id: str) -> str:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)

    def refund(self, payment, amount: Decimal) -> GatewayRefundResult:
        raise GatewayNotImplementedError(_NOT_IMPLEMENTED)
