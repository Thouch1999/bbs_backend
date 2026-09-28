"""
Every payment provider implements this same interface, so payments/services.py
never branches on which gateway it's talking to. See docs/SRS.md §8 (Step 8):
where the real API contract is uncertain, isolate the unknowns behind clearly
marked TODOs rather than guessing at endpoints or signature formats.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class GatewayCallbackResult:
    """The normalized shape every gateway's verify_callback() must produce."""

    merchant_ref: str  # our Payment.public_id, as we sent it when initiating
    provider_txn_id: str
    succeeded: bool
    raw: dict = field(default_factory=dict)


@dataclass
class GatewayRefundResult:
    success: bool
    provider_refund_id: str
    raw: dict = field(default_factory=dict)


class GatewayError(Exception):
    """Base for gateway-specific failures."""

    code = "gateway_error"


class InvalidSignatureError(GatewayError):
    """The webhook's signature didn't match — reject and log, never process."""

    code = "invalid_signature"


class GatewayNotImplementedError(GatewayError):
    """
    Raised by a real gateway stub (ABA PayWay / Wing / ACLEDA) whose actual
    merchant API contract isn't available yet — see the TODOs in that
    gateway's module. Never silently fall back to guessed behavior.
    """

    code = "gateway_not_implemented"


class PaymentGateway(ABC):
    provider_name: str

    @abstractmethod
    def initiate(self, payment) -> dict:
        """Returns whatever payload the client needs to complete payment
        (a redirect URL, a QR payload, ...)."""

    @abstractmethod
    def verify_callback(self, request) -> GatewayCallbackResult:
        """Verifies the webhook's signature and normalizes its payload.
        Raises InvalidSignatureError if the signature doesn't check out."""

    @abstractmethod
    def query_status(self, provider_txn_id: str) -> str:
        """Polls the gateway directly for a transaction's current status —
        used for reconciliation (Step 10), not the normal webhook path."""

    @abstractmethod
    def refund(self, payment, amount: Decimal) -> GatewayRefundResult:
        """Issues a full or partial refund against a succeeded payment."""
