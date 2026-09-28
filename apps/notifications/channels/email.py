"""
Email delivery via Django's own mail backend, so dev/test get console output
for free from EMAIL_BACKEND (settings/development.py) with zero extra code
here — production.py points the same backend at real SMTP.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .base import ChannelError, ChannelSendResult, NotificationChannel


class EmailChannel(NotificationChannel):
    channel_name = "email"

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> ChannelSendResult:
        message = EmailMultiAlternatives(
            subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient]
        )
        for filename, content, mimetype in attachments or []:
            message.attach(filename, content, mimetype)

        try:
            sent_count = message.send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001 — any backend/SMTP failure is a channel failure
            raise ChannelError(str(exc)) from exc

        return ChannelSendResult(success=sent_count > 0, raw={"sent_count": sent_count, "to": recipient})
