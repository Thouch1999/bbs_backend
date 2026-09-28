"""Support ticket lifecycle. Business logic only (CLAUDE.md hard rules)."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import SupportMessage, SupportTicket

# First-response targets per priority (the A5 SLA countdown).
SLA_HOURS = {
    SupportTicket.Priority.HIGH: 4,
    SupportTicket.Priority.NORMAL: 24,
    SupportTicket.Priority.LOW: 72,
}
# Payment trouble jumps the queue: money is stuck until someone looks.
_DEFAULT_PRIORITY = {
    SupportTicket.Category.PAYMENT_FAILED: SupportTicket.Priority.HIGH,
    SupportTicket.Category.REFUND_DISPUTE: SupportTicket.Priority.HIGH,
}


class SupportError(Exception):
    code = "support_error"


@transaction.atomic
def open_ticket(
    *, category, subject, body, contact_phone, contact_name="", user=None, booking=None, language="km"
):
    now = timezone.now()
    priority = _DEFAULT_PRIORITY.get(category, SupportTicket.Priority.NORMAL)
    ticket = SupportTicket.objects.create(
        user=user,
        booking=booking,
        contact_phone=contact_phone,
        contact_name=contact_name,
        language=language if language in ("km", "en") else "km",
        category=category,
        priority=priority,
        subject=subject,
        sla_due_at=now + timedelta(hours=SLA_HOURS[priority]),
        last_message_at=now,
    )
    SupportMessage.objects.create(
        ticket=ticket, author=user, sender=SupportMessage.Sender.CUSTOMER, body=body
    )
    return ticket


@transaction.atomic
def add_customer_message(ticket: SupportTicket, *, body: str, user=None) -> SupportMessage:
    if ticket.status == SupportTicket.Status.RESOLVED:
        # A customer writing back re-opens the conversation.
        ticket.status = SupportTicket.Status.OPEN
        ticket.resolved_at = None
    message = SupportMessage.objects.create(
        ticket=ticket, author=user, sender=SupportMessage.Sender.CUSTOMER, body=body
    )
    ticket.last_message_at = message.created_at
    ticket.unread_by_staff = True
    ticket.save(update_fields=["status", "resolved_at", "last_message_at", "unread_by_staff", "updated_at"])
    return message


@transaction.atomic
def reply_as_staff(ticket: SupportTicket, *, body: str, staff, resolve: bool = False) -> SupportMessage:
    """Posts a staff reply and notifies the customer (SMS to the ticket's
    contact phone, in the ticket's language)."""
    message = SupportMessage.objects.create(
        ticket=ticket,
        author=staff,
        sender=SupportMessage.Sender.STAFF,
        body=body,
        channel=SupportMessage.Channel.SMS,
    )
    ticket.last_message_at = message.created_at
    ticket.unread_by_staff = False
    ticket.status = SupportTicket.Status.RESOLVED if resolve else SupportTicket.Status.WAITING_CUSTOMER
    ticket.resolved_at = message.created_at if resolve else None
    ticket.save(update_fields=["last_message_at", "unread_by_staff", "status", "resolved_at", "updated_at"])

    from apps.notifications import services as notification_services

    notification_services.notify_support_reply(ticket, message)
    return message


def update_ticket(ticket: SupportTicket, **changes) -> SupportTicket:
    fields = []
    if "priority" in changes and changes["priority"] != ticket.priority:
        ticket.priority = changes["priority"]
        # Re-target the SLA from when the ticket was opened.
        ticket.sla_due_at = ticket.created_at + timedelta(hours=SLA_HOURS[ticket.priority])
        fields += ["priority", "sla_due_at"]
    if "status" in changes:
        ticket.status = changes["status"]
        ticket.resolved_at = timezone.now() if ticket.status == SupportTicket.Status.RESOLVED else None
        fields += ["status", "resolved_at"]
    if changes.get("mark_read"):
        ticket.unread_by_staff = False
        fields.append("unread_by_staff")
    if fields:
        ticket.save(update_fields=[*fields, "updated_at"])
    return ticket
