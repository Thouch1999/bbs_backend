from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OperatorStaff, User
from apps.core.audit import log_action
from apps.core.models import AuditLog

from .models import Operator, OperatorReviewNote


class AlreadyOperatorStaffError(Exception):
    pass


class OperatorReviewError(Exception):
    code = "operator_review_error"


@transaction.atomic
def register_operator(user, **operator_fields) -> Operator:
    if OperatorStaff.objects.filter(user=user).exists():
        raise AlreadyOperatorStaffError("This account is already linked to an operator.")

    operator = Operator.objects.create(status=Operator.Status.PENDING, **operator_fields)
    OperatorStaff.objects.create(
        user=user,
        operator=operator,
        staff_role=OperatorStaff.StaffRole.OWNER,
        can_create_bookings=True,
        can_validate_tickets=False,
    )
    previous_role = user.role
    user.role = User.Role.OPERATOR_STAFF
    user.save(update_fields=["role", "updated_at"])
    log_action(
        actor=user,
        action=AuditLog.Action.ROLE_CHANGED,
        target=user,
        before={"role": previous_role},
        after={"role": user.role},
    )
    return operator


class StaffManagementError(Exception):
    code = "staff_management_error"


class EmailAlreadyRegisteredError(StaffManagementError):
    code = "email_already_registered"


class ProtectedStaffMemberError(StaffManagementError):
    code = "protected_staff_member"


# Roles an owner/manager may create from the back office (playbook Step 15:
# "create counter-agent and conductor accounts"). Owner/manager accounts are
# not self-service — that would let a manager mint peers with full access.
CREATABLE_STAFF_ROLES = (OperatorStaff.StaffRole.COUNTER_AGENT, OperatorStaff.StaffRole.CONDUCTOR)

_DEFAULT_PERMISSIONS = {
    OperatorStaff.StaffRole.COUNTER_AGENT: {"can_create_bookings": True, "can_validate_tickets": False},
    OperatorStaff.StaffRole.CONDUCTOR: {"can_create_bookings": False, "can_validate_tickets": True},
}


def _staff_snapshot(staff: OperatorStaff) -> dict:
    return {
        "staff_role": staff.staff_role,
        "can_create_bookings": staff.can_create_bookings,
        "can_validate_tickets": staff.can_validate_tickets,
        "is_active": staff.user.is_active,
    }


@transaction.atomic
def create_staff_member(
    operator: Operator,
    *,
    actor,
    email: str,
    full_name: str,
    password: str,
    staff_role: str,
    can_create_bookings: bool | None = None,
    can_validate_tickets: bool | None = None,
) -> OperatorStaff:
    """
    Creates a brand-new login for a counter agent or conductor. Refuses an
    email that already has an account — silently attaching an existing
    passenger's account to an operator would hand staff access over
    someone else's login.
    """
    if staff_role not in CREATABLE_STAFF_ROLES:
        raise StaffManagementError(f"Staff role '{staff_role}' cannot be created here.")
    if User.objects.filter(email=email).exists():
        raise EmailAlreadyRegisteredError("An account with this email already exists.")

    defaults = _DEFAULT_PERMISSIONS[staff_role]
    user = User.objects.create_user(
        email=email, password=password, full_name=full_name, role=User.Role.OPERATOR_STAFF
    )
    staff = OperatorStaff.objects.create(
        user=user,
        operator=operator,
        staff_role=staff_role,
        can_create_bookings=(
            defaults["can_create_bookings"] if can_create_bookings is None else can_create_bookings
        ),
        can_validate_tickets=(
            defaults["can_validate_tickets"] if can_validate_tickets is None else can_validate_tickets
        ),
    )
    log_action(
        actor=actor,
        action=AuditLog.Action.ROLE_CHANGED,
        target=user,
        before={"role": None},
        after={"role": user.role, **_staff_snapshot(staff)},
    )
    return staff


@transaction.atomic
def update_staff_member(staff: OperatorStaff, *, actor, **changes) -> OperatorStaff:
    """
    Toggles permissions / deactivates a staff member. Owners and managers
    are read-only here, and nobody edits their own row — both would let a
    staff member lock the operator out of its own back office.
    """
    if staff.staff_role not in CREATABLE_STAFF_ROLES or staff.user_id == actor.id:
        raise ProtectedStaffMemberError("This staff member cannot be changed from here.")

    before = _staff_snapshot(staff)
    staff_fields = []
    for field in ("staff_role", "can_create_bookings", "can_validate_tickets"):
        if field in changes:
            if field == "staff_role" and changes[field] not in CREATABLE_STAFF_ROLES:
                raise StaffManagementError(f"Staff role '{changes[field]}' cannot be assigned here.")
            setattr(staff, field, changes[field])
            staff_fields.append(field)
    if staff_fields:
        staff.save(update_fields=[*staff_fields, "updated_at"])

    user_fields = []
    if "full_name" in changes:
        staff.user.full_name = changes["full_name"]
        user_fields.append("full_name")
    if "is_active" in changes:
        # Deactivate rather than delete: bookings keep booked_by_staff and
        # boarding records keep scanned_by pointing at this account.
        staff.user.is_active = changes["is_active"]
        user_fields.append("is_active")
    if user_fields:
        staff.user.save(update_fields=[*user_fields, "updated_at"])

    after = _staff_snapshot(staff)
    if after != before:
        log_action(
            actor=actor, action=AuditLog.Action.ROLE_CHANGED, target=staff.user, before=before, after=after
        )
    return staff


def approve_operator(operator: Operator, admin_user, commission_rate=None, note: str = "") -> Operator:
    """The audit record carries the admin's note (playbook Step 16 "Done
    when": approval writes an audit record with the admin's reason)."""
    if operator.status == Operator.Status.APPROVED:
        raise OperatorReviewError("This operator is already approved.")
    before = {"status": operator.status, "commission_rate": str(operator.commission_rate)}
    operator.status = Operator.Status.APPROVED
    operator.approved_at = timezone.now()
    operator.approved_by = admin_user
    operator.rejection_reason = ""
    if commission_rate is not None:
        operator.commission_rate = commission_rate
    updated_fields = [
        "status", "approved_at", "approved_by", "rejection_reason", "commission_rate", "updated_at",
    ]
    operator.save(update_fields=updated_fields)
    log_action(
        actor=admin_user,
        action=AuditLog.Action.OPERATOR_APPROVED,
        target=operator,
        before=before,
        after={"status": operator.status, "commission_rate": str(operator.commission_rate), "note": note},
    )
    if note:
        OperatorReviewNote.objects.create(operator=operator, author=admin_user, body=note)
    return operator


def reject_operator(operator: Operator, admin_user, reason: str) -> Operator:
    if operator.status == Operator.Status.REJECTED:
        raise OperatorReviewError("This operator is already rejected.")
    before = {"status": operator.status}
    operator.status = Operator.Status.REJECTED
    operator.rejection_reason = reason
    operator.approved_at = None
    operator.approved_by = admin_user
    operator.save(update_fields=["status", "rejection_reason", "approved_at", "approved_by", "updated_at"])
    log_action(
        actor=admin_user,
        action=AuditLog.Action.OPERATOR_REJECTED,
        target=operator,
        before=before,
        after={"status": operator.status, "reason": reason},
    )
    return operator



REVIEW_CHECK_KEYS = ("licence_number", "route_licence", "bank_account", "owner_id")
REVIEW_CHECK_VALUES = ("verified", "needs_info", "rejected")


def set_review_checklist(operator: Operator, checklist: dict) -> Operator:
    operator.review_checklist = {**operator.review_checklist, **checklist}
    operator.save(update_fields=["review_checklist", "updated_at"])
    return operator


def add_review_note(operator: Operator, admin_user, *, body: str, kind: str = OperatorReviewNote.Kind.NOTE):
    return OperatorReviewNote.objects.create(operator=operator, author=admin_user, body=body, kind=kind)


def suspend_operator(operator: Operator, admin_user, reason: str) -> Operator:
    """Takes an approved operator off the marketplace. Their writes stop
    immediately (IsApprovedOperator); existing trips and bookings stay."""
    if operator.status != Operator.Status.APPROVED:
        raise OperatorReviewError("Only an approved operator can be suspended.")
    operator.status = Operator.Status.SUSPENDED
    operator.rejection_reason = reason
    operator.save(update_fields=["status", "rejection_reason", "updated_at"])
    log_action(
        actor=admin_user,
        action=AuditLog.Action.OPERATOR_SUSPENDED,
        target=operator,
        before={"status": Operator.Status.APPROVED},
        after={"status": operator.status, "reason": reason},
    )
    return operator


def reinstate_operator(operator: Operator, admin_user, note: str = "") -> Operator:
    if operator.status != Operator.Status.SUSPENDED:
        raise OperatorReviewError("Only a suspended operator can be reinstated.")
    operator.status = Operator.Status.APPROVED
    operator.rejection_reason = ""
    operator.save(update_fields=["status", "rejection_reason", "updated_at"])
    log_action(
        actor=admin_user,
        action=AuditLog.Action.OPERATOR_REINSTATED,
        target=operator,
        before={"status": Operator.Status.SUSPENDED},
        after={"status": operator.status, "note": note},
    )
    return operator
