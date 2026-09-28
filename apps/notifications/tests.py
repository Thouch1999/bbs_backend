import itertools
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User
from apps.bookings import services as booking_services
from apps.fleet.models import Bus, Seat, SeatLayout
from apps.operators.models import Operator
from apps.payments.models import Payment, Refund
from apps.routes.models import City, Route
from apps.trips import services as trip_services
from apps.trips.models import Trip

from . import services, tasks
from .channels import ChannelError, get_channel
from .eticket import generate_ticket_pdf
from .models import Notification

pytestmark = pytest.mark.django_db

_plate_counter = itertools.count(1)


def _make_trip(num_seats=4, departure_in_hours=72, extra_minutes=0):
    operator = Operator.objects.create(name="Test Operator", contact_phone="+85511111111", status="approved")
    origin = City.objects.create(name_en="Phnom Penh", name_km="ភ្នំពេញ")
    destination = City.objects.create(name_en="Siem Reap", name_km="សៀមរាប")
    route = Route.objects.create(operator=operator, origin_city=origin, destination_city=destination)
    layout = SeatLayout.objects.create(operator=operator, name="Layout")
    Seat.objects.bulk_create(
        [
            Seat(seat_layout=layout, seat_number=f"A{i}", deck=1, row_position=i, col_position=1)
            for i in range(1, num_seats + 1)
        ]
    )
    # bulk_create doesn't populate .id on real MySQL (only MariaDB/Postgres
    # support RETURNING there) — refetch so seats[i].id is always usable.
    seats = list(Seat.objects.filter(seat_layout=layout).order_by("id"))
    bus = Bus.objects.create(
        operator=operator, seat_layout=layout, plate_number=f"PP-{next(_plate_counter):04d}"
    )
    departure_at = timezone.now() + timedelta(hours=departure_in_hours, minutes=extra_minutes)
    trip = Trip.objects.create(
        route=route,
        bus=bus,
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=6),
        base_fare_usd="10.00",
        base_fare_khr="41000",
    )
    trip_services.generate_trip_seats(trip)
    return trip, seats


def _confirmed_booking(trip, seats, *, user=None, contact_email="rider@example.test"):
    seat_ids = [seats[0].id]
    token = trip_services.hold_seats(trip.id, seat_ids, session_key="guest")
    booking = booking_services.create_booking(
        trip.id,
        token,
        [{"full_name": "Sok Dara", "age": 30, "gender": "male", "phone": "+85512345678"}],
        contact_phone="012345678",
        contact_email=contact_email,
        user=user,
    )
    return booking_services.confirm_booking(booking)


def _confirmation_notification(booking, channel=Notification.Channel.SMS):
    return Notification.objects.get(
        booking=booking, template=Notification.Template.BOOKING_CONFIRMATION, channel=channel
    )


class TestDevModeIsConsoleOnly:
    def test_default_settings_never_call_out_externally(self):
        assert settings.SMS_PROVIDER == "console"
        assert settings.TELEGRAM_BOT_TOKEN == ""
        # pytest-django forces EMAIL_BACKEND to locmem for the test run itself;
        # development.py (what actually runs outside pytest) points at console.
        assert "smtp" not in settings.EMAIL_BACKEND

    def test_sms_channel_console_mode_succeeds_without_raising(self):
        result = get_channel("sms").send(recipient="+85512345678", subject="", body="hello")
        assert result.success
        assert result.raw["console"] is True

    def test_telegram_channel_console_mode_succeeds_without_raising(self):
        result = get_channel("telegram").send(recipient="12345", subject="", body="hello")
        assert result.success
        assert result.raw["console"] is True


class TestBookingConfirmedWiring:
    def test_confirm_booking_queues_confirmation_and_receipt_style_notifications(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)

        notifications = Notification.objects.filter(
            booking=booking, template=Notification.Template.BOOKING_CONFIRMATION
        )
        assert notifications.filter(channel=Notification.Channel.SMS).exists()
        assert notifications.filter(channel=Notification.Channel.EMAIL).exists()
        assert notifications.count() == 2  # no telegram: no chat id linked

    def test_language_follows_user_preferred_language(self):
        trip, seats = _make_trip()
        user = User.objects.create_user(
            email="user17000000@bbms.test", password="Str0ngPassw0rd!", preferred_language=User.Language.EN
        )
        booking = _confirmed_booking(trip, seats, user=user)

        notification = _confirmation_notification(booking)
        assert notification.language == User.Language.EN

    def test_guest_checkout_defaults_to_khmer(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats, user=None)

        notification = _confirmation_notification(booking)
        assert notification.language == Notification.Language.KM

    def test_no_email_notification_queued_without_contact_email(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats, contact_email=None)

        notifications = Notification.objects.filter(
            booking=booking, template=Notification.Template.BOOKING_CONFIRMATION
        )
        assert not notifications.filter(channel=Notification.Channel.EMAIL).exists()
        assert notifications.filter(channel=Notification.Channel.SMS).exists()


class TestPaymentAndRefundWiring:
    def test_counter_payment_marked_paid_queues_receipt(self):
        trip, seats = _make_trip()
        token = trip_services.hold_seats(trip.id, [seats[0].id], session_key="guest")
        booking = booking_services.create_booking(
            trip.id,
            token,
            [{"full_name": "Sok Dara"}],
            contact_phone="012345678",
        )
        payment = Payment.objects.create(
            booking=booking,
            provider=Payment.Provider.COUNTER,
            amount=booking.total_amount,
            currency=booking.currency,
        )

        from apps.payments import services as payment_services

        payment_services.mark_counter_payment_paid(payment)

        assert Notification.objects.filter(
            booking=booking, template=Notification.Template.PAYMENT_RECEIPT
        ).exists()

    def test_refund_processed_queues_notification(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)
        payment = Payment.objects.create(
            booking=booking,
            provider=Payment.Provider.MOCK,
            status=Payment.Status.SUCCEEDED,
            amount=booking.total_amount,
            currency=booking.currency,
        )

        from apps.payments import services as payment_services

        payment_services.create_refund(payment, Decimal("5.00"), reason="passenger request")

        refund = Refund.objects.get(payment=payment)
        assert Notification.objects.filter(
            booking=booking, template=Notification.Template.REFUND_PROCESSED, payload__amount="5.00"
        ).exists()
        assert refund.status == Refund.Status.SUCCEEDED


class TestTripStatusChangeWiring:
    def test_delaying_a_trip_notifies_confirmed_bookings_only(self):
        trip, seats = _make_trip()
        confirmed = _confirmed_booking(trip, seats)
        token = trip_services.hold_seats(trip.id, [seats[1].id], session_key="guest2")
        pending = booking_services.create_booking(
            trip.id, token, [{"full_name": "Not Yet Paid"}], contact_phone="099999999"
        )

        trip_services.set_trip_status(trip, status=Trip.Status.DELAYED, delay_minutes=45)

        assert Notification.objects.filter(
            booking=confirmed, template=Notification.Template.TRIP_DELAYED
        ).exists()
        assert not Notification.objects.filter(
            booking=pending, template=Notification.Template.TRIP_DELAYED
        ).exists()

    def test_cancelling_a_trip_notifies_confirmed_bookings(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)

        trip_services.set_trip_status(trip, status=Trip.Status.CANCELLED, cancellation_reason="bus breakdown")

        notification = Notification.objects.get(
            booking=booking, template=Notification.Template.TRIP_CANCELLED, channel=Notification.Channel.SMS
        )
        assert notification.payload["cancellation_reason"] == "bus breakdown"


class TestDepartureReminders:
    """
    The reminder window is [now + N hours, now + N hours + 5min) — a couple of
    minutes' slack (extra_minutes=2) keeps these tests off the lower boundary,
    since `departure_at` is fixed at trip-creation time but the window itself
    is recomputed (fractionally later) when send_departure_reminders() runs.
    """

    def test_sends_once_for_a_trip_in_the_window(self):
        trip, seats = _make_trip(departure_in_hours=settings.DEPARTURE_REMINDER_HOURS_BEFORE, extra_minutes=2)
        booking = _confirmed_booking(trip, seats)

        sent = services.send_departure_reminders()

        assert sent == 1
        assert Notification.objects.filter(
            booking=booking, template=Notification.Template.DEPARTURE_REMINDER
        ).exists()

    def test_never_sent_twice_for_the_same_booking(self):
        trip, seats = _make_trip(departure_in_hours=settings.DEPARTURE_REMINDER_HOURS_BEFORE, extra_minutes=2)
        _confirmed_booking(trip, seats)

        first_run = services.send_departure_reminders()
        second_run = services.send_departure_reminders()

        assert first_run == 1
        assert second_run == 0

    def test_trip_outside_the_window_is_not_reminded(self):
        trip, seats = _make_trip(departure_in_hours=settings.DEPARTURE_REMINDER_HOURS_BEFORE + 5)
        _confirmed_booking(trip, seats)

        assert services.send_departure_reminders() == 0


class TestSendNotificationTask:
    def test_sends_and_marks_status(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)
        notification = _confirmation_notification(booking)

        tasks.send_notification_task(notification.id)

        notification.refresh_from_db()
        assert notification.status == Notification.Status.SENT
        assert notification.sent_at is not None

    def test_booking_confirmation_email_attaches_the_pdf_eticket(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)
        notification = _confirmation_notification(booking, channel=Notification.Channel.EMAIL)

        from django.core import mail

        tasks.send_notification_task(notification.id)

        notification.refresh_from_db()
        assert notification.status == Notification.Status.SENT
        sent_email = mail.outbox[-1]
        assert len(sent_email.attachments) == 1
        filename, content, mimetype = sent_email.attachments[0]
        assert filename == f"ticket-{booking.pnr}.pdf"
        assert content.startswith(b"%PDF")

    def test_replaying_an_already_sent_notification_is_a_no_op(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)
        notification = _confirmation_notification(booking)
        tasks.send_notification_task(notification.id)
        sent_at = Notification.objects.get(id=notification.id).sent_at

        tasks.send_notification_task(notification.id)

        assert Notification.objects.get(id=notification.id).sent_at == sent_at

    def test_channel_failure_marks_failed_and_schedules_a_retry(
        self, monkeypatch, settings, django_capture_on_commit_callbacks
    ):
        """
        confirm_booking's own notification dispatch is deferred via
        transaction.on_commit, so it must be run explicitly via
        django_capture_on_commit_callbacks — it does not fire inline just
        because this test made no further DB changes afterward. (An earlier
        version of this test relied on it firing immediately, which only
        appeared to happen because the dev DB's tables were MyISAM: Django's
        own supports_transactions probe creates a table with no explicit
        ENGINE=, so it inherited that non-transactional default and reported
        supports_transactions=False, silently downgrading every django_db
        test's isolation to real-commit/no-atomic-wrapping instead of the
        intended rollback-per-test. Step 18 fixed the engine — see
        config/settings/base.py's init_command and git history.)
        """
        settings.NOTIFICATION_MAX_RETRIES = 2

        def _boom(*, recipient, subject, body, attachments=None):
            raise ChannelError("provider unreachable")

        monkeypatch.setattr("apps.notifications.channels.SMSChannel.send", lambda self, **kw: _boom(**kw))

        trip, seats = _make_trip()
        with django_capture_on_commit_callbacks(execute=True):
            booking = _confirmed_booking(trip, seats)

        notification = _confirmation_notification(booking)
        assert notification.status == Notification.Status.FAILED
        assert notification.retry_count == 2  # scheduled retry re-ran eagerly until max_retries hit
        assert notification.error_message == "provider unreachable"


class TestETicketPdf:
    def test_generates_a_pdf_with_khmer_and_english(self):
        trip, seats = _make_trip()
        booking = _confirmed_booking(trip, seats)

        km_pdf = generate_ticket_pdf(booking, language="km")
        en_pdf = generate_ticket_pdf(booking, language="en")

        assert km_pdf.startswith(b"%PDF")
        assert en_pdf.startswith(b"%PDF")
