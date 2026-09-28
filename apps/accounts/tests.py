import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.services import (
    ExpiredResetCodeError,
    InvalidPhoneNumberError,
    InvalidResetCodeError,
    TooManyAttemptsError,
    normalize_phone,
    request_password_reset_code,
    verify_password_reset_code,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


class TestNormalizePhone:
    """normalize_phone/PhoneField remain in use for contact-phone fields
    (bookings/operators/support) — not for auth, which is email-only."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("012345678", "+85512345678"),
            ("+855 12 345 678", "+85512345678"),
            ("855 12 345 678", "+85512345678"),
            ("012-345-678", "+85512345678"),
            ("0912345678", "+855912345678"),
        ],
    )
    def test_valid_numbers(self, raw, expected):
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize("raw", ["123", "not-a-phone", "+85512", "0"])
    def test_invalid_numbers_raise(self, raw):
        with pytest.raises(InvalidPhoneNumberError):
            normalize_phone(raw)


class TestPasswordResetServiceFlow:
    """Direct service-level coverage of the reset-code happy/wrong/expired paths."""

    def test_happy_path(self, settings):
        settings.DEBUG = True
        email = "reset@bbms.test"
        request_password_reset_code(email)
        assert len(mail.outbox) == 1
        sent_code = mail.outbox[0].body.split("code is ")[1].split(".")[0]
        verify_password_reset_code(email, sent_code)  # does not raise

    def test_wrong_code_is_rejected(self):
        email = "reset@bbms.test"
        request_password_reset_code(email)
        with pytest.raises(InvalidResetCodeError):
            verify_password_reset_code(email, "000000")

    def test_expired_or_never_requested_is_rejected(self):
        with pytest.raises(ExpiredResetCodeError):
            verify_password_reset_code("reset@bbms.test", "123456")

    def test_too_many_attempts_locks_out(self, settings):
        settings.OTP_MAX_ATTEMPTS = 2
        email = "reset@bbms.test"
        request_password_reset_code(email)
        for _ in range(2):
            with pytest.raises(InvalidResetCodeError):
                verify_password_reset_code(email, "000000")
        with pytest.raises(TooManyAttemptsError):
            verify_password_reset_code(email, "000000")


class TestPasswordResetEndpoints:
    def _request_reset_code(self, api_client, settings, email):
        settings.DEBUG = True
        User.objects.create_user(email=email, password="Str0ngPassw0rd!")
        api_client.post("/api/v1/auth/password-reset/", {"email": email}, format="json")
        return mail.outbox[-1].body.split("code is ")[1].split(".")[0]

    def test_reset_code_confirm_sets_a_new_password(self, api_client, settings):
        email = "reset1@bbms.test"
        code = self._request_reset_code(api_client, settings, email)

        resp = api_client.post(
            "/api/v1/auth/password-reset/confirm/",
            {"email": email, "code": code, "new_password": "NewStr0ngPass!"},
            format="json",
        )

        assert resp.status_code == 200
        user = User.objects.get(email=email)
        assert user.check_password("NewStr0ngPass!")

    def test_locked_account_cannot_reset_its_password(self, api_client, settings):
        email = "reset2@bbms.test"
        code = self._request_reset_code(api_client, settings, email)
        User.objects.filter(email=email).update(is_active=False)

        resp = api_client.post(
            "/api/v1/auth/password-reset/confirm/",
            {"email": email, "code": code, "new_password": "NewStr0ngPass!"},
            format="json",
        )

        assert resp.status_code == 403
        assert resp.data["error"]["code"] == "account_locked"

    def test_reset_confirm_rejects_a_wrong_code(self, api_client, settings):
        email = "reset3@bbms.test"
        self._request_reset_code(api_client, settings, email)

        resp = api_client.post(
            "/api/v1/auth/password-reset/confirm/",
            {"email": email, "code": "000000", "new_password": "NewStr0ngPass!"},
            format="json",
        )

        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "reset_code_invalid"

    def test_reset_confirm_for_an_unknown_email_does_not_reveal_that(self, api_client, settings):
        settings.DEBUG = True
        email = "no-such-account@bbms.test"
        api_client.post("/api/v1/auth/password-reset/", {"email": email}, format="json")
        # PasswordResetRequestView guards against enumeration: no account
        # exists here, so no email was ever sent, and confirming reports it
        # as expired/never requested rather than revealing anything.
        assert len(mail.outbox) == 0

        resp = api_client.post(
            "/api/v1/auth/password-reset/confirm/",
            {"email": email, "code": "000000", "new_password": "NewStr0ngPass!"},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "reset_code_expired"


class TestProfileEndpoint:
    def test_authenticated_user_can_read_their_own_profile(self, api_client):
        user = User.objects.create_user(email="me@bbms.test", password="Str0ngPassw0rd!", full_name="Me")
        api_client.force_authenticate(user)
        resp = api_client.get("/api/v1/profile/")
        assert resp.status_code == 200
        assert resp.data["full_name"] == "Me"


class TestAuthThrottling:
    """
    Step 11: confirm the "otp"/"login" ScopedRateThrottle rates declared in
    settings (REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]) actually bite,
    end-to-end — not just that they're declared.
    """

    def test_password_reset_request_is_throttled_after_the_configured_rate(self, api_client, settings):
        settings.DEBUG = True  # dev console log needs it on
        otp_rate = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["otp"].split("/")[0])

        for i in range(otp_rate):
            resp = api_client.post("/api/v1/auth/password-reset/", {"email": f"throttle{i}@bbms.test"})
            assert resp.status_code == 200

        resp = api_client.post("/api/v1/auth/password-reset/", {"email": "throttle-last@bbms.test"})
        assert resp.status_code == 429

    def test_login_is_throttled_after_the_configured_rate(self, api_client, settings):
        login_rate = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"].split("/")[0])
        User.objects.create_user(email="throttlelogin@bbms.test", password="Str0ngPassw0rd!")

        for _ in range(login_rate):
            resp = api_client.post(
                "/api/v1/auth/login/",
                {"email": "throttlelogin@bbms.test", "password": "wrong-password"},
            )
            assert resp.status_code == 401

        resp = api_client.post(
            "/api/v1/auth/login/", {"email": "throttlelogin@bbms.test", "password": "wrong-password"}
        )
        assert resp.status_code == 429

    def test_anonymous_trip_search_is_throttled_by_the_default_anon_rate(self, api_client, settings):
        """Confirms DEFAULT_THROTTLE_CLASSES actually applies AnonRateThrottle
        to an ordinary endpoint, not just the scoped auth ones."""
        anon_rate = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"].split("/")[0])

        for _ in range(anon_rate):
            resp = api_client.get("/api/v1/trips/search/")
            assert resp.status_code == 200

        resp = api_client.get("/api/v1/trips/search/")
        assert resp.status_code == 429


class TestRoleBasedAccess:
    def test_anonymous_cannot_read_profile(self, api_client):
        resp = api_client.get("/api/v1/profile/")
        assert resp.status_code == 401

    def test_passenger_cannot_access_admin_operator_list(self, api_client):
        user = User.objects.create_user(email="passenger1@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(user)
        resp = api_client.get("/api/v1/operators/")
        assert resp.status_code == 403

    def test_admin_can_access_operator_list(self, api_client):
        admin = User.objects.create_user(
            email="admin1@bbms.test", password="Str0ngPassw0rd!", role=User.Role.ADMIN
        )
        api_client.force_authenticate(admin)
        resp = api_client.get("/api/v1/operators/")
        assert resp.status_code == 200

    def test_passenger_cannot_access_operator_me(self, api_client):
        user = User.objects.create_user(email="passenger2@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(user)
        resp = api_client.get("/api/v1/operators/me/")
        assert resp.status_code == 403


class TestRegisterEndpoint:
    def test_register_creates_a_user_and_returns_tokens(self, api_client):
        resp = api_client.post(
            "/api/v1/auth/register/",
            {"email": "newuser@bbms.test", "password": "Str0ngPassw0rd!", "full_name": "New User"},
            format="json",
        )
        assert resp.status_code == 201
        assert "access" in resp.data and "refresh" in resp.data
        assert User.objects.filter(email="newuser@bbms.test").exists()

    def test_cannot_register_twice_with_the_same_email(self, api_client):
        User.objects.create_user(email="dup@bbms.test", password="Str0ngPassw0rd!")
        resp = api_client.post(
            "/api/v1/auth/register/",
            {"email": "dup@bbms.test", "password": "Str0ngPassw0rd!", "full_name": "Dup"},
            format="json",
        )
        assert resp.status_code == 400


class TestLogoutEndpoint:
    def test_logout_blacklists_the_refresh_token(self, api_client):
        user = User.objects.create_user(email="logout1@bbms.test", password="Str0ngPassw0rd!")
        login = api_client.post(
            "/api/v1/auth/login/",
            {"email": "logout1@bbms.test", "password": "Str0ngPassw0rd!"},
            format="json",
        )
        refresh = login.data["refresh"]

        api_client.force_authenticate(user)
        resp = api_client.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")
        assert resp.status_code == 205

        replay = api_client.post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
        assert replay.status_code == 401

    def test_logout_requires_a_refresh_token(self, api_client):
        user = User.objects.create_user(email="logout2@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(user)
        resp = api_client.post("/api/v1/auth/logout/", {}, format="json")
        assert resp.status_code == 400

    def test_logout_rejects_an_already_blacklisted_token(self, api_client):
        user = User.objects.create_user(email="logout3@bbms.test", password="Str0ngPassw0rd!")
        login = api_client.post(
            "/api/v1/auth/login/",
            {"email": "logout3@bbms.test", "password": "Str0ngPassw0rd!"},
            format="json",
        )
        refresh = login.data["refresh"]
        api_client.force_authenticate(user)
        api_client.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")

        resp = api_client.post("/api/v1/auth/logout/", {"refresh": refresh}, format="json")
        assert resp.status_code == 400


class TestSavedPassengers:
    def test_owner_can_crud_their_own_saved_passengers(self, api_client):
        user = User.objects.create_user(email="saved1@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(user)

        created = api_client.post(
            "/api/v1/saved-passengers/", {"full_name": "Sok Dara", "age": 30, "gender": "male"}, format="json"
        )
        assert created.status_code == 201
        public_id = created.data["public_id"]

        listed = api_client.get("/api/v1/saved-passengers/")
        assert listed.status_code == 200 and len(listed.data["results"]) == 1

        other = User.objects.create_user(email="saved2@bbms.test", password="Str0ngPassw0rd!")
        api_client.force_authenticate(other)
        assert api_client.get("/api/v1/saved-passengers/").data["results"] == []

        api_client.force_authenticate(user)
        deleted = api_client.delete(f"/api/v1/saved-passengers/{public_id}/")
        assert deleted.status_code == 204


class TestAdminUserManagement:
    def _admin(self, email="admin2@bbms.test"):
        return User.objects.create_user(email=email, password="Str0ngPassw0rd!", role=User.Role.ADMIN)

    def test_list_filters_by_search_role_and_is_active(self, api_client):
        admin = self._admin()
        User.objects.create_user(email="findable@bbms.test", password="x", full_name="Findable Person")
        User.objects.create_user(email="inactive1@bbms.test", password="x", is_active=False)
        api_client.force_authenticate(admin)

        by_search = api_client.get("/api/v1/admin/users/", {"search": "Findable"}).data["results"]
        assert [u["full_name"] for u in by_search] == ["Findable Person"]

        by_role = api_client.get("/api/v1/admin/users/", {"role": "admin"}).data["results"]
        assert [u["email"] for u in by_role] == [admin.email]

        by_active = api_client.get("/api/v1/admin/users/", {"is_active": "false"}).data["results"]
        assert [u["email"] for u in by_active] == ["inactive1@bbms.test"]

    def test_detail_view_returns_a_single_user(self, api_client):
        admin = self._admin()
        target = User.objects.create_user(email="target1@bbms.test", password="x", full_name="Target")
        api_client.force_authenticate(admin)
        resp = api_client.get(f"/api/v1/admin/users/{target.public_id}/")
        assert resp.status_code == 200 and resp.data["full_name"] == "Target"

    def test_admin_can_lock_and_change_another_users_role(self, api_client):
        admin = self._admin()
        target = User.objects.create_user(email="target2@bbms.test", password="x")
        api_client.force_authenticate(admin)

        resp = api_client.patch(
            f"/api/v1/admin/users/{target.public_id}/", {"is_active": False, "role": "admin"}, format="json"
        )
        assert resp.status_code == 200
        target.refresh_from_db()
        assert target.is_active is False and target.role == "admin" and target.is_staff is True

    def test_admin_cannot_change_their_own_account(self, api_client):
        admin = self._admin()
        api_client.force_authenticate(admin)
        resp = api_client.patch(
            f"/api/v1/admin/users/{admin.public_id}/", {"is_active": False}, format="json"
        )
        assert resp.status_code == 400
        assert resp.data["error"]["code"]
