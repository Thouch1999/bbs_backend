"""
SMS delivery. No Cambodian SMS aggregator contract exists yet, so — like
apps.payments.gateways.aba_payway — the real HTTP call is a clearly marked
TODO. `settings.SMS_PROVIDER = "console"` (the dev/test default) logs instead,
per CLAUDE.md/playbook: dev mode must never call out externally.
"""

import logging

from django.conf import settings

from .base import ChannelError, ChannelSendResult, NotificationChannel

logger = logging.getLogger("bbms.notifications.sms")


class SMSChannel(NotificationChannel):
    channel_name = "sms"

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> ChannelSendResult:
        if settings.SMS_PROVIDER == "console":
            logger.info("SMS -> %s: %s", recipient, body)
            return ChannelSendResult(success=True, raw={"console": True, "to": recipient, "body": body})

        # TODO: wire up the real aggregator once a provider/contract is
        # chosen (SMS_PROVIDER_API_KEY / SMS_SENDER_ID are already in
        # settings for this). Never guess at an endpoint or auth scheme.
        raise ChannelError(
            f"SMS provider '{settings.SMS_PROVIDER}' is not implemented yet — see the TODO in sms.py."
        )
