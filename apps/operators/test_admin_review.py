"""Step 16: admin operator review (A2) and user management."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.core.factories import client_for, make_operator, make_staff
from apps.core.models import AuditLog
from apps.operators.models import Operator

pytestmark = pytest.mark.django_db


def _admin(email="user17000002@bbms.test"):
    return User.objects.create_user(email=email, password="x", role=User.Role.ADMIN, is_staff=True)


class TestOperatorApproval:
    def test_approval_audit_record_carries_the_admins_reason(self):
        """Done-when: operator approval writes an audit record with the admin's reason."""
        operator = make_operator(status=Operator.Status.PENDING)
        admin = _admin()
        resp = client_for(admin).post(
            f"/api/v1/operators/{operator.public_id}/approve/",
            {"commission_rate": "12.50", "note": "Licence and bank account verified in person."},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert resp.data["status"] == "approved"
        log = AuditLog.objects.get(action="operator_approved", target_id=str(operator.public_id))
        assert log.actor == admin
        assert log.after["note"] == "Licence and bank account verified in person."
        assert log.after["commission_rate"] == "12.50"
        assert resp.data["review_notes"][0]["body"] == "Licence and bank account verified in person."

    def test_reject_reason_is_audited_and_double_approval_refused(self):
        operator = make_operator(status=Operator.Status.PENDING)
        client = client_for(_admin())
        client.post(
            f"/api/v1/operators/{operator.public_id}/reject/", {"reason": "Licence expired"}, format="json"
        )
        log = AuditLog.objects.get(action="operator_rejected")
        assert log.after["reason"] == "Licence expired"
        approved = make_operator()
        resp = client.post(f"/api/v1/operators/{approved.public_id}/approve/", {}, format="json")
        assert resp.status_code == 400

    def test_checklist_notes_and_info_request_visible_to_owner(self):
        operator = make_operator(status=Operator.Status.PENDING)
        owner = make_staff(operator)
        client = client_for(_admin())
        resp = client.patch(
            f"/api/v1/operators/{operator.public_id}/checklist/",
            {"checklist": {"licence_number": "verified", "bank_account": "needs_info"}},
            format="json",
        )
        assert resp.data["review_checklist"] == {"licence_number": "verified", "bank_account": "needs_info"}
        bad = client.patch(
            f"/api/v1/operators/{operator.public_id}/checklist/",
            {"checklist": {"shoe_size": "verified"}},
            format="json",
        )
        assert bad.status_code == 400
        client.post(
            f"/api/v1/operators/{operator.public_id}/notes/",
            {"kind": "info_request", "body": "Please upload a clearer bank statement."},
            format="json",
        )
        client.post(
            f"/api/v1/operators/{operator.public_id}/notes/",
            {"body": "Internal: called owner."},
            format="json",
        )
        me = client_for(owner).get("/api/v1/operators/me/").data
        assert [r["body"] for r in me["info_requests"]] == ["Please upload a clearer bank statement."]

    def test_suspend_blocks_writes_then_reinstate(self):
        operator = make_operator()
        owner = make_staff(operator)
        admin_client = client_for(_admin())
        admin_client.post(
            f"/api/v1/operators/{operator.public_id}/suspend/", {"reason": "Complaints"}, format="json"
        )
        operator.refresh_from_db()
        assert operator.status == "suspended"
        assert (
            client_for(owner).post("/api/v1/seat-layouts/", {"name": "x"}, format="json").status_code == 403
        )
        admin_client.post(f"/api/v1/operators/{operator.public_id}/reinstate/", {}, format="json")
        operator.refresh_from_db()
        assert operator.status == "approved"
        assert AuditLog.objects.filter(action__in=["operator_suspended", "operator_reinstated"]).count() == 2

    def test_queue_search_and_counts(self):
        make_operator(status=Operator.Status.PENDING)
        target = Operator.objects.create(name="Capitol Tours", contact_phone="+85512345678", status="pending")
        rows = client_for(_admin()).get("/api/v1/operators/", {"status": "pending", "search": "capitol"}).data
        assert [r["public_id"] for r in rows["results"]] == [str(target.public_id)]
        assert rows["results"][0]["routes_count"] == 0


class TestUserAdmin:
    def test_lock_revokes_sessions_blocks_password_reset_and_is_audited(self):
        admin = _admin()
        user = User.objects.create_user(email="user12888999@bbms.test", password="Str0ngPassw0rd!")
        refresh = RefreshToken.for_user(user)
        resp = client_for(admin).patch(
            f"/api/v1/admin/users/{user.public_id}/", {"is_active": False}, format="json"
        )
        assert resp.status_code == 200 and resp.data["is_active"] is False
        assert BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        assert AuditLog.objects.filter(action="user_locked", target_id=str(user.public_id)).exists()

        # A correct reset code (verification stubbed — codes are stored
        # hashed) must still not let a locked account back in.
        client = APIClient()
        with patch("apps.accounts.views.verify_password_reset_code", return_value=None):
            resp = client.post(
                "/api/v1/auth/password-reset/confirm/",
                {"email": user.email, "code": "123456", "new_password": "NewStr0ngPassw0rd!"},
                format="json",
            )
        assert resp.status_code == 403
        assert resp.data["error"]["code"] == "account_locked"
        login = client.post(
            "/api/v1/auth/login/", {"email": user.email, "password": "Str0ngPassw0rd!"}, format="json"
        )
        assert login.status_code == 401

    def test_role_rules(self):
        admin = _admin()
        client = client_for(admin)
        passenger = User.objects.create_user(email="user12888000@bbms.test", password="x")
        resp = client.patch(f"/api/v1/admin/users/{passenger.public_id}/", {"role": "admin"}, format="json")
        assert resp.data["role"] == "admin"
        staff = make_staff(make_operator())
        assert (
            client.patch(
                f"/api/v1/admin/users/{staff.public_id}/", {"role": "passenger"}, format="json"
            ).status_code
            == 400
        )
        assert (
            client.patch(
                f"/api/v1/admin/users/{admin.public_id}/", {"is_active": False}, format="json"
            ).status_code
            == 400
        )

    def test_search(self):
        User.objects.create_user(email="user12777111@bbms.test", password="x", full_name="Sok Dara")
        rows = client_for(_admin()).get("/api/v1/admin/users/", {"search": "dara"}).data["results"]
        assert [r["full_name"] for r in rows] == ["Sok Dara"]
