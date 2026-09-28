"""
Every delivery channel implements this same interface, so
notifications/services.py never branches on which channel it's talking to —
mirrors apps.payments.gateways.base's PaymentGateway pattern.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChannelSendResult:
    success: bool
    raw: dict = field(default_factory=dict)


class ChannelError(Exception):
    """Base for channel-specific delivery failures."""


class NotificationChannel(ABC):
    channel_name: str

    @abstractmethod
    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> ChannelSendResult:
        """
        Delivers one message. `attachments` is a list of
        (filename, content_bytes, mimetype) — only the email channel uses it
        (the e-ticket PDF); other channels ignore it. Raises ChannelError on
        failure — never swallows it, so services.send_notification can record it.
        """
