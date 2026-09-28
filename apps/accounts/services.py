import re
import secrets
import string

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache

_CAMBODIA_NATIONAL_LENGTH = range(8, 10)  # 8 or 9 digits after the leading 0 / country code


class InvalidPhoneNumberError(ValueError):
    pass


class PasswordResetError(Exception):
    code = "password_reset_error"
    message = "Password reset error."


class ExpiredResetCodeError(PasswordResetError):
    code = "reset_code_expired"
    message = "This code has expired. Request a new one."


class InvalidResetCodeError(PasswordResetError):
    code = "reset_code_invalid"
    message = "Incorrect code."


class TooManyAttemptsError(PasswordResetError):
    code = "reset_too_many_attempts"
    message = "Too many incorrect attempts. Request a new code."


def normalize_phone(raw: str) -> str:
    """
    Normalizes a Cambodian phone number to E.164 (+855...). Accepts local
    (012345678), international with or without a leading +, and numbers with
    spaces/dashes.
    """
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+855"):
        national = digits[4:]
    elif digits.startswith("855") and len(digits) > 9:
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits

    if not national.isdigit() or len(national) not in _CAMBODIA_NATIONAL_LENGTH:
        raise InvalidPhoneNumberError(f"'{raw}' is not a valid Cambodian phone number.")
    return f"+855{national}"


def _reset_key(email: str) -> str:
    return f"password_reset:{email}"


def _reset_attempts_key(email: str) -> str:
    return f"password_reset:{email}:attempts"


def request_password_reset_code(email: str) -> None:
    """Generates a hashed reset code, caches it, and emails it to the user."""
    from apps.notifications.channels.email import EmailChannel

    code = "".join(secrets.choice(string.digits) for _ in range(settings.OTP_LENGTH))
    cache.set(_reset_key(email), make_password(code), timeout=settings.OTP_TTL_SECONDS)
    cache.set(_reset_attempts_key(email), 0, timeout=settings.OTP_TTL_SECONDS)

    if settings.DEBUG:
        print(f"[PASSWORD RESET] email={email} code={code}")

    EmailChannel().send(
        recipient=email,
        subject="BBMS password reset code",
        body=(
            f"Your BBMS password reset code is {code}. It expires in "
            f"{settings.OTP_TTL_SECONDS // 60} minutes. If you didn't request this, ignore this email."
        ),
    )


def verify_password_reset_code(email: str, code: str) -> None:
    """
    Raises ExpiredResetCodeError / TooManyAttemptsError / InvalidResetCodeError
    on failure. Returns None and consumes the code on success.
    """
    hash_key = _reset_key(email)
    attempts_key = _reset_attempts_key(email)

    hashed = cache.get(hash_key)
    if hashed is None:
        raise ExpiredResetCodeError()

    attempts = cache.get(attempts_key) or 0
    if attempts >= settings.OTP_MAX_ATTEMPTS:
        cache.delete(hash_key)
        cache.delete(attempts_key)
        raise TooManyAttemptsError()

    if not check_password(code, hashed):
        cache.incr(attempts_key)
        raise InvalidResetCodeError()

    cache.delete(hash_key)
    cache.delete(attempts_key)



# --- Admin user management (Step 16) -----------------------------------------
class UserAdminError(Exception):
    code = "user_admin_error"


# Operator staff get their role through operators.register_operator /
# create_staff_member (which also create the OperatorStaff row); an admin
# only ever moves an account between passenger and admin here.
ADMIN_ASSIGNABLE_ROLES = ("passenger", "admin")


def _revoke_sessions(user) -> None:
    """Blacklist every outstanding refresh token so a locked account is
    logged out everywhere, not just refused on its next request."""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def update_user_as_admin(user, *, actor, is_active=None, role=None):
    from django.db import transaction

    from apps.core.audit import log_action
    from apps.core.models import AuditLog

    from .models import User

    if user.pk == actor.pk:
        raise UserAdminError("You can't change your own account from the admin panel.")
    with transaction.atomic():
        if role is not None and role != user.role:
            if role not in ADMIN_ASSIGNABLE_ROLES or user.role == User.Role.OPERATOR_STAFF:
                raise UserAdminError("Operator staff roles are managed from the operator's own staff screen.")
            before = user.role
            user.role = role
            user.is_staff = role == User.Role.ADMIN
            user.save(update_fields=["role", "is_staff", "updated_at"])
            log_action(
                actor=actor,
                action=AuditLog.Action.ROLE_CHANGED,
                target=user,
                before={"role": before},
                after={"role": role},
            )
            _revoke_sessions(user)  # the JWT carries the old role claim
        if is_active is not None and is_active != user.is_active:
            user.is_active = is_active
            user.save(update_fields=["is_active", "updated_at"])
            log_action(
                actor=actor,
                action=AuditLog.Action.USER_UNLOCKED if is_active else AuditLog.Action.USER_LOCKED,
                target=user,
                before={"is_active": not is_active},
                after={"is_active": is_active},
            )
            if not is_active:
                _revoke_sessions(user)
    return user
