"""
Telegram delivery. Per docs/DECISIONS.md #4, Telegram is a notification
channel from Step 9 onward — full bot-driven booking is out of scope for v1.
No bot has been registered yet, so `settings.TELEGRAM_BOT_TOKEN` empty (the
dev/test default) logs instead of calling the real Bot API.
"""

import logging

from django.conf import settings

from .base import ChannelError, ChannelSendResult, NotificationChannel

logger = logging.getLogger("bbms.notifications.telegram")


class TelegramChannel(NotificationChannel):
    channel_name = "telegram"

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> ChannelSendResult:
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.info("Telegram -> chat %s: %s", recipient, body)
            return ChannelSendResult(success=True, raw={"console": True, "chat_id": recipient, "body": body})

        # TODO: call https://api.telegram.org/bot<token>/sendMessage (and
        # sendDocument for the e-ticket PDF) once a bot is registered. Never
        # guess at the request shape.
        raise ChannelError(
            "Telegram bot is configured but sendMessage is not implemented yet — see the TODO."
        )
