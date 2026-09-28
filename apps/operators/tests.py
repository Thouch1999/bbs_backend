import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.operators.models import Operator

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


def _register_operator(api_client, user):
    api_client.force_authenticate(user)
    return api_client.post(
        "/api/v1/operators/register/",
        {
            "name": "Golden Bus",
            "name_km": "ចក្រទនុមមាស",
            "contact_phone": "012999888",
            "contact_email": "ops@goldenbus.example",
        },
    )


class TestOperatorRegistration:
    def test_register_creates_pending_operator_and_owner_staff(self, api_client):
        user = User.objects.create_user(email="user12121212@bbms.test", password="Str0ngPassw0rd!")
        resp = _register_operator(api_client, user)
        assert resp.status_code == 201
        assert resp.data["status"] == Operator.Status.PENDING

        user.refresh_from_db()
        assert user.role == User.Role.OPERATOR_STAFF
        assert user.operator_staff.staff_role == "owner"

    def test_cannot_register_twice(self, api_client):
        user = User.objects.create_user(email="user12121213@bbms.test", password="Str0ngPassw0rd!")
        _register_operator(api_client, user)
        resp = _register_operator(api_client, user)
        assert resp.status_code == 400


class TestLicenceDocumentValidation:
    """Step 17 hardening: reject unsafe operator licence uploads."""

    def _register_with_document(self, api_client, user, upload):
        api_client.force_authenticate(user)
        return api_client.post(
            "/api/v1/operators/register/",
            {
                "name": "Golden Bus",
                "name_km": "ចក្រទនុមមាស",
                "contact_phone": "012999888",
                "contact_email": "ops@goldenbus.example",
                "licence_document": upload,
            },
            format="multipart",
        )

    def test_rejects_disallowed_extension(self, api_client):
        user = User.objects.create_user(email="user12121214@bbms.test", password="Str0ngPassw0rd!")
        upload = SimpleUploadedFile("licence.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        resp = self._register_with_document(api_client, user, upload)
        assert resp.status_code == 400
        assert "licence_document" in resp.data.get("error", {}).get("details", resp.data)

    def test_rejects_content_that_does_not_match_its_extension(self, api_client):
        user = User.objects.create_user(email="user12121215@bbms.test", password="Str0ngPassw0rd!")
        # .pdf extension, but no %PDF- magic bytes — e.g. a renamed script.
        upload = SimpleUploadedFile("licence.pdf", b"<script>evil()</script>", content_type="application/pdf")
        resp = self._register_with_document(api_client, user, upload)
        assert resp.status_code == 400

    def test_rejects_oversized_document(self, api_client, settings):
        from apps.core import validators as core_validators

        settings_backup = core_validators.MAX_LICENCE_DOCUMENT_SIZE_BYTES
        core_validators.MAX_LICENCE_DOCUMENT_SIZE_BYTES = 10
        try:
            user = User.objects.create_user(email="user12121216@bbms.test", password="Str0ngPassw0rd!")
            upload = SimpleUploadedFile("licence.pdf", b"%PDF-" + b"0" * 100, content_type="application/pdf")
            resp = self._register_with_document(api_client, user, upload)
            assert resp.status_code == 400
        finally:
            core_validators.MAX_LICENCE_DOCUMENT_SIZE_BYTES = settings_backup

    def test_accepts_a_genuine_pdf(self, api_client):
        user = User.objects.create_user(email="user12121217@bbms.test", password="Str0ngPassw0rd!")
        upload = SimpleUploadedFile("licence.pdf", b"%PDF-1.4\n...", content_type="application/pdf")
        resp = self._register_with_document(api_client, user, upload)
        assert resp.status_code == 201
        assert resp.data["licence_document"]


class TestPendingOperatorAccessDenied:
    """A pending operator's owner can see their own status but not run the business."""

    def _pending_owner(self, api_client):
        user = User.objects.create_user(email="user12121214@bbms.test", password="Str0ngPassw0rd!")
        _register_operator(api_client, user)
        api_client.force_authenticate(user)
        return user

    def test_pending_owner_can_view_own_operator(self, api_client):
        self._pending_owner(api_client)
        resp = api_client.get("/api/v1/operators/me/")
        assert resp.status_code == 200
        assert resp.data["status"] == Operator.Status.PENDING

    def test_pending_owner_cannot_update_operator_profile(self, api_client):
        self._pending_owner(api_client)
        resp = api_client.patch("/api/v1/operators/me/", {"name": "New Name"})
        assert resp.status_code == 403

    def test_approved_owner_can_update_operator_profile(self, api_client):
        user = self._pending_owner(api_client)
        operator = user.operator_staff.operator
        operator.status = Operator.Status.APPROVED
        operator.save(update_fields=["status"])

        resp = api_client.patch("/api/v1/operators/me/", {"name": "Golden Express"})
        assert resp.status_code == 200
        assert resp.data["name"] == "Golden Express"

    def test_pending_owner_cannot_list_all_operators(self, api_client):
        self._pending_owner(api_client)
        resp = api_client.get("/api/v1/operators/")
        assert resp.status_code == 403


class TestOperatorApproval:
    def test_admin_approve_flow(self, api_client):
        owner = User.objects.create_user(email="user12121215@bbms.test", password="Str0ngPassw0rd!")
        _register_operator(api_client, owner)
        operator = owner.operator_staff.operator

        admin = User.objects.create_user(
            email="user12121216@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )
        api_client.force_authenticate(admin)

        resp = api_client.post(
            f"/api/v1/operators/{operator.public_id}/approve/", {"commission_rate": "8.50"}
        )
        assert resp.status_code == 200
        assert resp.data["status"] == Operator.Status.APPROVED
        assert resp.data["commission_rate"] == "8.50"

    def test_admin_reject_flow_requires_reason(self, api_client):
        owner = User.objects.create_user(email="user12121217@bbms.test", password="Str0ngPassw0rd!")
        _register_operator(api_client, owner)
        operator = owner.operator_staff.operator

        admin = User.objects.create_user(
            email="user12121218@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )
        api_client.force_authenticate(admin)

        resp = api_client.post(f"/api/v1/operators/{operator.public_id}/reject/", {})
        assert resp.status_code == 400

        resp = api_client.post(
            f"/api/v1/operators/{operator.public_id}/reject/", {"reason": "Licence document unreadable."}
        )
        assert resp.status_code == 200
        assert resp.data["status"] == Operator.Status.REJECTED

    def test_non_admin_cannot_approve(self, api_client):
        owner = User.objects.create_user(email="user12121219@bbms.test", password="Str0ngPassw0rd!")
        _register_operator(api_client, owner)
        operator = owner.operator_staff.operator

        other_user = User.objects.create_user(email="user12121220@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(other_user)
        resp = api_client.post(f"/api/v1/operators/{operator.public_id}/approve/", {})
        assert resp.status_code == 403
