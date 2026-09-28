"""
Fans a domain event (booking confirmed, payment received, trip delayed/
cancelled, refund processed, departure approaching) out to a Notification row
per channel/recipient, then hands each off to Celery. Business logic only —
apps.bookings/payments/trips call these functions from their own services.py,
never from views, per CLAUDE.md's hard rules.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking

from .models import Notification


def _language_for(user) -> str:
    return user.preferred_language if user is not None else Notification.Language.KM


def _route_label(route, language: str) -> str:
    if route.name:
        return route.name
    if language == "en":
        return f"{route.origin_city.name_en} → {route.destination_city.name_en}"
    return f"{route.origin_city.name_km} → {route.destination_city.name_km}"


def _lead_passenger_name(booking: Booking) -> str:
    passenger = booking.passengers.order_by("id").first()
    return passenger.full_name if passenger else booking.contact_phone


def _queue(
    *, channel, recipient, language, template, context, booking=None, trip=None
) -> Notification | None:
    if not recipient:
        return None

    notification = Notification.objects.create(
        channel=channel,
        recipient=recipient,
        language=language,
        template=template,
        payload=context,
        booking=booking,
        trip=trip,
    )
    # Deferred import: tasks.py imports this module for send_departure_reminders.
    from . import tasks

    # Never let the worker pick this up before the row (and whatever DB
    # change triggered it, e.g. confirm_booking's status flip) is actually
    # committed — CLAUDE.md's transaction-boundary rule applies here too,
    # not just to payment-gateway calls.
    transaction.on_commit(lambda: tasks.send_notification_task.delay(notification.id))
    return notification


def _recipients_for_booking(booking: Booking) -> list[tuple[str, str]]:
    """(channel, recipient) for every channel this booking actually has a
    recipient for: SMS to contact_phone always, email if given, Telegram if
    the account linked a chat id. Shared by the real fan-out and the
    operator's pre-send preview so their counts can't disagree."""
    recipients = [
        (Notification.Channel.SMS, booking.contact_phone),
        (Notification.Channel.EMAIL, booking.contact_email or ""),
    ]
    if booking.user_id and booking.user.telegram_chat_id:
        recipients.append((Notification.Channel.TELEGRAM, booking.user.telegram_chat_id))
    return [(channel, recipient) for channel, recipient in recipients if recipient]


def _queue_for_booking(booking: Booking, template: str, context: dict) -> list[Notification]:
    language = _language_for(booking.user)
    notifications = [
        _queue(
            channel=channel,
            recipient=recipient,
            language=language,
            template=template,
            context=context,
            booking=booking,
            trip=booking.trip,
        )
        for channel, recipient in _recipients_for_booking(booking)
    ]
    return [n for n in notifications if n is not None]


def notify_booking_confirmed(booking: Booking) -> list[Notification]:
    trip = booking.trip
    language = _language_for(booking.user)
    context = {
        "passenger_name": _lead_passenger_name(booking),
        "pnr": booking.pnr,
        "route_name": _route_label(trip.route, language),
        "departure_at": timezone.localtime(trip.departure_at).strftime("%Y-%m-%d %H:%M"),
        "total_amount": str(booking.total_amount),
        "currency": booking.currency,
    }
    return _queue_for_booking(booking, Notification.Template.BOOKING_CONFIRMATION, context)


def notify_payment_receipt(payment) -> list[Notification]:
    booking = payment.booking
    context = {
        "passenger_name": _lead_passenger_name(booking),
        "pnr": booking.pnr,
        "total_amount": str(payment.amount),
        "currency": payment.currency,
        "provider": payment.get_provider_display(),
    }
    return _queue_for_booking(booking, Notification.Template.PAYMENT_RECEIPT, context)


def _trip_status_bookings(trip):
    return trip.bookings.filter(status=Booking.Status.CONFIRMED).select_related("user")


def _trip_delayed_context(trip, booking: Booking, delay_minutes: int) -> dict:
    new_departure = trip.departure_at + timedelta(minutes=delay_minutes or 0)
    return {
        "passenger_name": _lead_passenger_name(booking),
        "pnr": booking.pnr,
        "delay_minutes": delay_minutes or 0,
        "departure_at": timezone.localtime(new_departure).strftime("%Y-%m-%d %H:%M"),
    }


def _trip_cancelled_context(booking: Booking, cancellation_reason: str, language: str) -> dict:
    return {
        "passenger_name": _lead_passenger_name(booking),
        "pnr": booking.pnr,
        "cancellation_reason": cancellation_reason
        or ("ហេតុផលប្រតិបត្តិការ" if language == "km" else "an operational issue"),
    }


def notify_trip_delayed(trip) -> list[Notification]:
    sent = []
    for booking in _trip_status_bookings(trip):
        context = _trip_delayed_context(trip, booking, trip.delay_minutes)
        sent.extend(_queue_for_booking(booking, Notification.Template.TRIP_DELAYED, context))
    return sent


def notify_trip_cancelled(trip) -> list[Notification]:
    sent = []
    for booking in _trip_status_bookings(trip):
        context = _trip_cancelled_context(booking, trip.cancellation_reason, _language_for(booking.user))
        sent.extend(_queue_for_booking(booking, Notification.Template.TRIP_CANCELLED, context))
    return sent


def preview_trip_status_notifications(
    trip, *, status: str, delay_minutes: int | None = None, cancellation_reason: str = ""
) -> dict:
    """
    What set_trip_status(trip, status=...) *would* send, without sending or
    saving anything: how many bookings/passengers get notified, per-channel
    message counts, and the exact rendered message text (km and en) for the
    first affected booking. Uses the same recipient rule and context
    builders as notify_trip_delayed/cancelled, so the operator's
    confirmation dialog can't disagree with what actually goes out.
    """
    from apps.trips.models import Trip

    from .templates import render

    empty = {"bookings_count": 0, "passengers_count": 0, "channels": {}, "messages": {}}
    if status == Trip.Status.DELAYED:
        template = Notification.Template.TRIP_DELAYED
    elif status == Trip.Status.CANCELLED:
        template = Notification.Template.TRIP_CANCELLED
    else:
        return empty  # back on time notifies nobody

    bookings = list(_trip_status_bookings(trip).prefetch_related("passengers"))
    channels = {channel: 0 for channel in Notification.Channel.values}
    for booking in bookings:
        for channel, _recipient in _recipients_for_booking(booking):
            channels[channel] += 1

    messages = {}
    if bookings:
        sample = bookings[0]
        for language in ("km", "en"):
            if template == Notification.Template.TRIP_DELAYED:
                context = _trip_delayed_context(trip, sample, delay_minutes)
            else:
                context = _trip_cancelled_context(sample, cancellation_reason, language)
            messages[language] = render(template, language, context)["body"]

    return {
        "bookings_count": len(bookings),
        "passengers_count": sum(len(b.passengers.all()) for b in bookings),
        "channels": channels,
        "messages": messages,
    }


def notify_support_reply(ticket, message) -> list[Notification]:
    """SMS the staff reply to the ticket's contact phone, in the language the
    customer chose (the A5 composer's flag shows which one)."""
    context = {
        "customer_name": ticket.contact_name or ticket.contact_phone,
        "subject": ticket.subject,
        "reply": message.body[:600],
    }
    notification = _queue(
        channel=Notification.Channel.SMS,
        recipient=ticket.contact_phone,
        language=ticket.language,
        template=Notification.Template.SUPPORT_REPLY,
        context=context,
        booking=ticket.booking,
    )
    return [notification] if notification else []


def notify_refund_processed(refund) -> list[Notification]:
    booking = refund.payment.booking
    context = {
        "passenger_name": _lead_passenger_name(booking),
        "pnr": booking.pnr,
        "amount": str(refund.amount),
        "currency": refund.payment.currency,
    }
    return _queue_for_booking(booking, Notification.Template.REFUND_PROCESSED, context)


def send_departure_reminders() -> int:
    """
    Beat task body (crontab minute="*/5" — see config/celery.py). A trip
    falls in the window exactly once per run, and a booking is skipped once
    it already has a DEPARTURE_REMINDER row, so a trip is never reminded
    twice even if a run is retried or overlaps the next tick.
    """
    from apps.trips.models import Trip

    now = timezone.now()
    window_start = now + timedelta(hours=settings.DEPARTURE_REMINDER_HOURS_BEFORE)
    window_end = window_start + timedelta(minutes=5)

    trips = Trip.objects.filter(
        departure_at__gte=window_start,
        departure_at__lt=window_end,
        status__in=[Trip.Status.SCHEDULED, Trip.Status.DELAYED],
    )

    sent = 0
    for trip in trips:
        bookings = (
            trip.bookings.filter(status=Booking.Status.CONFIRMED)
            .exclude(notifications__template=Notification.Template.DEPARTURE_REMINDER)
            .select_related("user")
        )
        for booking in bookings:
            language = _language_for(booking.user)
            context = {
                "passenger_name": _lead_passenger_name(booking),
                "pnr": booking.pnr,
                "route_name": _route_label(trip.route, language),
                "departure_at": timezone.localtime(trip.departure_at).strftime("%Y-%m-%d %H:%M"),
            }
            _queue_for_booking(booking, Notification.Template.DEPARTURE_REMINDER, context)
            sent += 1
    return sent
