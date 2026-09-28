import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import services
from . import templates as notification_templates
from .channels import ChannelError, get_channel
from .models import Notification

logger = logging.getLogger("bbms.notifications.tasks")


@shared_task
def send_notification_task(notification_id):
    try:
        notification = Notification.objects.select_related("booking").get(id=notification_id)
    except Notification.DoesNotExist:
        logger.warning("send_notification_task: Notification %s no longer exists.", notification_id)
        return None

    if notification.status == Notification.Status.SENT:
        return notification.id  # idempotent: never re-deliver an already-sent message

    rendered = notification_templates.render(
        notification.template, notification.language, notification.payload
    )

    attachments = []
    if (
        notification.channel == Notification.Channel.EMAIL
        and notification.template == Notification.Template.BOOKING_CONFIRMATION
        and notification.booking_id
    ):
        from .eticket import generate_ticket_pdf

        attachments.append(
            (
                f"ticket-{notification.booking.pnr}.pdf",
                generate_ticket_pdf(notification.booking, language=notification.language),
                "application/pdf",
            )
        )

    channel = get_channel(notification.channel)
    try:
        result = channel.send(
            recipient=notification.recipient,
            subject=rendered["subject"],
            body=rendered["body"],
            attachments=attachments,
        )
    except ChannelError as exc:
        notification.retry_count += 1
        notification.status = Notification.Status.FAILED
        notification.error_message = str(exc)[:500]
        notification.save(update_fields=["retry_count", "status", "error_message", "updated_at"])
        if notification.retry_count < settings.NOTIFICATION_MAX_RETRIES:
            countdown = 10 * (2**notification.retry_count)  # exponential backoff
            send_notification_task.apply_async(args=[notification.id], countdown=countdown)
        return notification.id

    notification.status = Notification.Status.SENT if result.success else Notification.Status.FAILED
    notification.provider_response = result.raw
    notification.sent_at = timezone.now() if result.success else None
    notification.save(update_fields=["status", "provider_response", "sent_at", "updated_at"])
    return notification.id


@shared_task
def send_departure_reminders():
    return services.send_departure_reminders()
