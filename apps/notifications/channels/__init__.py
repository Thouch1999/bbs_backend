from .base import ChannelError, ChannelSendResult, NotificationChannel
from .email import EmailChannel
from .sms import SMSChannel
from .telegram import TelegramChannel

_CHANNELS: dict[str, type[NotificationChannel]] = {
    "sms": SMSChannel,
    "email": EmailChannel,
    "telegram": TelegramChannel,
}


def get_channel(channel_name: str) -> NotificationChannel:
    try:
        return _CHANNELS[channel_name]()
    except KeyError as exc:
        raise ChannelError(f"Unknown notification channel '{channel_name}'.") from exc


__all__ = ["ChannelError", "ChannelSendResult", "NotificationChannel", "get_channel"]
