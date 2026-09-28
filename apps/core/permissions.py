from rest_framework.permissions import SAFE_METHODS, BasePermission


def _operator_id_for(user):
    """
    Resolves the operator a staff user belongs to, or None. Centralized here
    so every operator-scoped permission/queryset agrees on how to find it.
    """
    if not user.is_authenticated or user.role != user.Role.OPERATOR_STAFF:
        return None
    staff = getattr(user, "operator_staff", None)
    return staff.operator_id if staff else None


def object_operator_id(obj):
    """
    The operator that owns `obj`, following the one relation hop each
    operator-scoped model uses: a direct `operator` FK (Route, Bus,
    SeatLayout), or via its route (Trip, RouteStop) or its trip's route
    (Booking). Kept here so object-level checks agree with the querysets in
    OperatorScopedViewSetMixin instead of assuming every model has
    `operator_id` — Trip/RouteStop don't, which used to 403 every
    operator trip detail/status call for the trip's own operator.
    """
    if hasattr(obj, "operator_id"):
        return obj.operator_id
    route = getattr(obj, "route", None)
    if route is not None:
        return route.operator_id
    trip = getattr(obj, "trip", None)
    if trip is not None:
        return trip.route.operator_id
    return None


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == user.Role.ADMIN)


class IsOperatorStaff(BasePermission):
    """Any staff member of any operator (owner, manager, counter agent, conductor)."""

    def has_permission(self, request, view):
        return _operator_id_for(request.user) is not None


class IsOperatorOwnerOrManager(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if _operator_id_for(user) is None:
            return False
        staff = user.operator_staff
        return staff.staff_role in ("owner", "manager")

    def has_object_permission(self, request, view, obj):
        operator_id = _operator_id_for(request.user)
        return operator_id is not None and operator_id == object_operator_id(obj)


class IsApprovedOperator(BasePermission):
    """
    Gates the operator-running endpoints (fleet/routes/schedule/bookings in
    Step 5+, and writes to the operator's own profile here in Step 4) behind
    admin approval. A pending/rejected/suspended operator's staff can still
    view their own application status, just not run the business.
    """

    message = "Your operator account is not approved yet."

    def has_permission(self, request, view):
        user = request.user
        if _operator_id_for(user) is None:
            return False
        return user.operator_staff.operator.status == user.operator_staff.operator.Status.APPROVED


class IsApprovedOperatorForWrites(IsApprovedOperator):
    """Reads stay open (a pending operator can set up and review its data
    model); writes that would put trips/fleet into the live marketplace
    need admin approval first."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class IsOperatorManagerOrCounterAgent(BasePermission):
    """Owner/manager, or any staff member allowed to sell tickets — the
    counter-booking screen and operator bookings list are used by both."""

    def has_permission(self, request, view):
        user = request.user
        if _operator_id_for(user) is None:
            return False
        staff = user.operator_staff
        return staff.staff_role in ("owner", "manager") or bool(staff.can_create_bookings)

    def has_object_permission(self, request, view, obj):
        operator_id = _operator_id_for(request.user)
        return operator_id is not None and operator_id == object_operator_id(obj)


class IsConductor(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if _operator_id_for(user) is None:
            return False
        return bool(user.operator_staff.can_validate_tickets)


class IsCounterAgent(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if _operator_id_for(user) is None:
            return False
        return bool(user.operator_staff.can_create_bookings)


class IsBookingOwnerOrStaff(BasePermission):
    """
    A booking is visible to: the passenger who owns it (request.user matches
    booking.user, when authenticated), staff of the operator that ran the
    trip, or an admin. Guest bookings are reached via PNR + phone lookup, not
    this permission class.
    """

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.role == user.Role.ADMIN:
            return True
        if getattr(obj, "user_id", None) == user.id:
            return True
        operator_id = _operator_id_for(user)
        return operator_id is not None and operator_id == obj.trip.route.operator_id


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS
