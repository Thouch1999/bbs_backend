from .aba_payway import ABAPayWayGateway
from .acleda import ACLEDAGateway
from .base import (
    GatewayCallbackResult,
    GatewayError,
    GatewayNotImplementedError,
    GatewayRefundResult,
    InvalidSignatureError,
    PaymentGateway,
)
from .counter import CounterGateway
from .demo import DemoGateway
from .mock import MockGateway
from .wing import WingGateway

_GATEWAYS = {
    "aba_payway": ABAPayWayGateway(),
    "wing": WingGateway(),
    "acleda": ACLEDAGateway(),
    "counter": CounterGateway(),
    "mock": MockGateway(),
    "demo": DemoGateway(),
}


def get_gateway(provider: str) -> PaymentGateway:
    try:
        return _GATEWAYS[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown payment provider: {provider}") from exc


__all__ = [
    "GatewayCallbackResult",
    "GatewayError",
    "GatewayNotImplementedError",
    "GatewayRefundResult",
    "InvalidSignatureError",
    "PaymentGateway",
    "get_gateway",
]
