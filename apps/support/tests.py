"""Step 16: content (A4) and support (A5) apps, admin overview, audit log."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.content.models import Announcement, Banner
from apps.core.factories import client_for, make_operator, make_trip, paid_booking
from apps.notifications.models import Notification
from apps.support import services as services_module
from apps.support.models import SupportTicket

pytestmark = pytest.mark.django_db


def _admin():
    return User.objects.create_user(
        email="user17000003@bbms.test", password="x", role=User.Role.ADMIN, is_staff=True
    )


class TestContent:
    def test_only_live_banners_are_public(self):
        now = timezone.now()
        live = Banner.objects.create(title_km="ក", title_en="Live", placement="home")
        Banner.objects.create(
            title_km="ក", title_en="Future", placement="home", starts_at=now + timedelta(days=1)
        )
        Banner.objects.create(title_km="ក", title_en="Off", placement="home", is_active=False)
        Banner.objects.create(title_km="ក", title_en="Search", placement="search")
        data = APIClient().get("/api/v1/content/banners/", {"placement": "home"}).data
        assert [b["public_id"] for b in data] == [str(live.public_id)]

    def test_announcements_by_audience(self):
        Announcement.objects.create(message_km="ក", message_en="All", audience="all")
        Announcement.objects.create(message_km="ក", message_en="Ops", audience="operators")
        data = APIClient().get("/api/v1/content/announcements/").data
        assert [a["message_en"] for a in data] == ["All"]

    def test_admin_crud_and_validation(self):
        client = client_for(_admin())
        body = {"title_km": "ក", "title_en": "Sale", "placement": "home", "background_color": "#f58220"}
        resp = client.post("/api/v1/admin/banners/", body, format="json")
        assert resp.status_code == 201 and resp.data["background_color"] == "#F58220"
        assert (
            client.post(
                "/api/v1/admin/banners/", {**body, "background_color": "orange"}, format="json"
            ).status_code
            == 400
        )
        assert APIClient().post("/api/v1/admin/banners/", body, format="json").status_code == 401


class TestSupport:
    def _passenger(self, email="user12666111@bbms.test", phone="+85512666111", language="en"):
        return User.objects.create_user(
            email=email, phone=phone, password="x", full_name="Chan", preferred_language=language
        )

    def test_passenger_opens_ticket_about_own_booking_only(self):
        passenger = self._passenger()
        booking = paid_booking(make_trip(make_operator()))
        booking.user = passenger
        booking.save(update_fields=["user"])
        other = paid_booking(make_trip(make_operator()))
        client = client_for(passenger)
        body = {"category": "refund_dispute", "subject": "Refund", "body": "Where is my refund?"}
        resp = client.post(
            "/api/v1/support/tickets/", {**body, "booking_id": str(booking.public_id)}, format="json"
        )
        assert resp.status_code == 201, resp.data
        assert resp.data["pnr"] == booking.pnr and resp.data["priority"] == "high"
        assert resp.data["language"] == "en"
        bad = client.post(
            "/api/v1/support/tickets/", {**body, "booking_id": str(other.public_id)}, format="json"
        )
        assert bad.status_code == 400

    def test_staff_reply_notifies_in_customer_language_and_customer_reply_reopens(self):
        passenger = self._passenger(language="km")
        client = client_for(passenger)
        ticket_id = client.post(
            "/api/v1/support/tickets/",
            {"category": "account", "subject": "Login", "body": "Can't log in"},
            format="json",
        ).data["public_id"]
        admin = client_for(_admin())
        resp = admin.post(
            f"/api/v1/admin/support/tickets/{ticket_id}/reply/",
            {"body": "Try again now.", "resolve": True},
            format="json",
        )
        assert resp.status_code == 201 and resp.data["status"] == "resolved"
        notification = Notification.objects.get(template="support_reply")
        assert notification.language == "km" and notification.recipient == passenger.phone
        client.post(f"/api/v1/support/tickets/{ticket_id}/", {"body": "Still broken"}, format="json")
        ticket = SupportTicket.objects.get(public_id=ticket_id)
        assert ticket.status == "open" and ticket.unread_by_staff is True

    def test_admin_can_reprioritize_resolve_and_mark_read(self):
        passenger = self._passenger()
        client = client_for(passenger)
        ticket_id = client.post(
            "/api/v1/support/tickets/",
            {"category": "other", "subject": "s", "body": "b"},
            format="json",
        ).data["public_id"]
        ticket = SupportTicket.objects.get(public_id=ticket_id)
        assert ticket.priority == "normal"
        original_sla_due_at = ticket.sla_due_at

        admin = client_for(_admin())

        resp = admin.patch(
            f"/api/v1/admin/support/tickets/{ticket_id}/", {"priority": "high"}, format="json"
        )
        assert resp.status_code == 200 and resp.data["priority"] == "high"
        ticket.refresh_from_db()
        assert ticket.priority == "high"
        # Re-targeted from created_at with the new priority's SLA hours, not
        # just left at the ticket's original (normal-priority) due time.
        assert ticket.sla_due_at != original_sla_due_at
        assert ticket.sla_due_at == ticket.created_at + timedelta(hours=services_module.SLA_HOURS["high"])

        resp = admin.patch(
            f"/api/v1/admin/support/tickets/{ticket_id}/", {"status": "resolved"}, format="json"
        )
        assert resp.status_code == 200 and resp.data["status"] == "resolved"
        ticket.refresh_from_db()
        assert ticket.status == "resolved" and ticket.resolved_at is not None

        # Reopening (status back to "open") clears resolved_at again.
        resp = admin.patch(f"/api/v1/admin/support/tickets/{ticket_id}/", {"status": "open"}, format="json")
        assert resp.status_code == 200
        ticket.refresh_from_db()
        assert ticket.status == "open" and ticket.resolved_at is None

        ticket.unread_by_staff = True
        ticket.save(update_fields=["unread_by_staff"])
        resp = admin.patch(
            f"/api/v1/admin/support/tickets/{ticket_id}/", {"mark_read": True}, format="json"
        )
        assert resp.status_code == 200
        ticket.refresh_from_db()
        assert ticket.unread_by_staff is False

    def test_update_ticket_with_no_changes_is_a_no_op(self):
        passenger = self._passenger()
        ticket = services_module.open_ticket(
            category="other", subject="s", body="b", contact_phone=passenger.phone, user=passenger
        )
        updated = services_module.update_ticket(ticket)
        assert updated.pk == ticket.pk
        assert updated.status == ticket.status

    def test_queue_orders_unresolved_by_sla_and_hides_other_customers(self):
        a, b = (
            self._passenger(email="user12666001@bbms.test"),
            self._passenger(email="user12666002@bbms.test"),
        )
        for user, category in ((a, "other"), (b, "payment_failed")):
            client_for(user).post(
                "/api/v1/support/tickets/", {"category": category, "subject": "s", "body": "b"}, format="json"
            )
        rows = client_for(_admin()).get("/api/v1/admin/support/tickets/").data["results"]
        assert [r["category"] for r in rows] == ["payment_failed", "other"]  # high-priority SLA is sooner
        assert len(client_for(a).get("/api/v1/support/tickets/").data) == 1


class TestAdminOverviewAndAudit:
    def test_overview_shape_and_system_status_modes(self):
        paid_booking(make_trip(make_operator()))
        data = client_for(_admin()).get("/api/v1/reports/admin/overview/").data
        assert data["totals"]["revenue"]["usd"] == "10.00"
        status = {row["key"]: row for row in data["system_status"]}
        assert status["aba_payway"]["mode"] == "not_implemented" and status["aba_payway"]["health"] == "down"
        assert status["mock"]["mode"] == "test" and status["mock"]["last_success_at"] is not None
        assert status["sms"]["mode"] == "console"
        assert len(data["series"]) == 1 and data["series"][0]["bookings"] == 1

    def test_audit_log_booking_filter(self):
        from apps.bookings import services as booking_services

        booking = paid_booking(make_trip(make_operator(), departure_in_hours=100))
        booking_services.cancel_booking(booking, reason="test")
        rows = client_for(_admin()).get("/api/v1/admin/audit-logs/", {"booking": str(booking.public_id)}).data
        assert [r["action"] for r in rows["results"]] == ["booking_cancelled"]
