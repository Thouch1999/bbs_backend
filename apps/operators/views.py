from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OperatorStaff
from apps.core.permissions import IsAdmin, IsApprovedOperator, IsOperatorOwnerOrManager
from apps.core.serializers import ErrorResponseSerializer

from .models import Operator
from .serializers import (
    AdminOperatorSerializer,
    OperatorApproveSerializer,
    OperatorRegisterSerializer,
    OperatorReinstateSerializer,
    OperatorRejectSerializer,
    OperatorReviewNoteSerializer,
    OperatorSerializer,
    OperatorSuspendSerializer,
    ReviewChecklistSerializer,
    ReviewNoteCreateSerializer,
    StaffMemberCreateSerializer,
    StaffMemberSerializer,
    StaffMemberUpdateSerializer,
)
from .services import (
    AlreadyOperatorStaffError,
    OperatorReviewError,
    StaffManagementError,
    add_review_note,
    approve_operator,
    create_staff_member,
    register_operator,
    reinstate_operator,
    reject_operator,
    set_review_checklist,
    suspend_operator,
    update_staff_member,
)


def _service_error_response(exc) -> Response:
    return Response({"error": {"code": exc.code, "message": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)


class OperatorRegisterView(generics.CreateAPIView):
    serializer_class = OperatorRegisterSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            operator = register_operator(request.user, **serializer.validated_data)
        except AlreadyOperatorStaffError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OperatorSerializer(operator).data, status=status.HTTP_201_CREATED)


def _admin_operators():
    return (
        Operator.objects.prefetch_related("staff__user", "review_notes__author")
        .annotate(
            staff_count=Count("staff", distinct=True),
            routes_count=Count("routes", distinct=True),
            buses_count=Count("buses", distinct=True),
            trips_count=Count("routes__trips", distinct=True),
        )
    )


class OperatorListView(generics.ListAPIView):
    """Admin queue of operators, filterable by ?status=pending|approved|rejected|suspended
    and ?search= (name, Khmer name, phone, licence number)."""

    serializer_class = AdminOperatorSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        qs = _admin_operators().order_by("-created_at")
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(name_km__icontains=search)
                | Q(contact_phone__icontains=search)
                | Q(licence_number__icontains=search)
            )
        return qs


class AdminOperatorDetailView(generics.RetrieveAPIView):
    serializer_class = AdminOperatorSerializer
    permission_classes = [IsAdmin]
    lookup_field = "public_id"

    def get_queryset(self):
        return _admin_operators()


def _admin_operator_response(operator):
    return Response(AdminOperatorSerializer(_admin_operators().get(pk=operator.pk)).data)


class OperatorChecklistView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=ReviewChecklistSerializer, responses=AdminOperatorSerializer)
    def patch(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = ReviewChecklistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_review_checklist(operator, serializer.validated_data["checklist"])
        return _admin_operator_response(operator)


class OperatorReviewNoteView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=ReviewNoteCreateSerializer, responses={201: OperatorReviewNoteSerializer})
    def post(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = ReviewNoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = add_review_note(operator, request.user, **serializer.validated_data)
        return Response(OperatorReviewNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class OperatorSuspendView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=OperatorSuspendSerializer, responses=AdminOperatorSerializer)
    def post(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = OperatorSuspendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            suspend_operator(operator, request.user, serializer.validated_data["reason"])
        except OperatorReviewError as exc:
            return _service_error_response(exc)
        return _admin_operator_response(operator)


class OperatorReinstateView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=OperatorReinstateSerializer, responses=AdminOperatorSerializer)
    def post(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = OperatorReinstateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reinstate_operator(operator, request.user, serializer.validated_data.get("note", ""))
        except OperatorReviewError as exc:
            return _service_error_response(exc)
        return _admin_operator_response(operator)


class OperatorApproveView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=OperatorApproveSerializer, responses=AdminOperatorSerializer)
    def post(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = OperatorApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            operator = approve_operator(operator, request.user, **serializer.validated_data)
        except OperatorReviewError as exc:
            return _service_error_response(exc)
        return _admin_operator_response(operator)


class OperatorRejectView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=OperatorRejectSerializer, responses=AdminOperatorSerializer)
    def post(self, request, public_id):
        operator = generics.get_object_or_404(Operator, public_id=public_id)
        serializer = OperatorRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            operator = reject_operator(operator, request.user, serializer.validated_data["reason"])
        except OperatorReviewError as exc:
            return _service_error_response(exc)
        return _admin_operator_response(operator)


class OperatorMeView(generics.RetrieveUpdateAPIView):
    """
    The calling operator staff member's own operator profile. Any
    owner/manager can view it (to check application status while pending);
    only an approved operator can edit it.
    """

    serializer_class = OperatorSerializer

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsOperatorOwnerOrManager()]
        return [IsOperatorOwnerOrManager(), IsApprovedOperator()]

    def get_object(self):
        return self.request.user.operator_staff.operator


class StaffListCreateView(APIView):
    """GET: every staff member of the caller's operator. POST: create a
    counter-agent or conductor login (owner/manager only; writes need an
    approved operator)."""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsOperatorOwnerOrManager()]
        return [IsOperatorOwnerOrManager(), IsApprovedOperator()]

    @extend_schema(responses=StaffMemberSerializer(many=True))
    def get(self, request):
        staff = (
            OperatorStaff.objects.filter(operator=request.user.operator_staff.operator)
            .select_related("user")
            .order_by("created_at")
        )
        return Response(StaffMemberSerializer(staff, many=True).data)

    @extend_schema(
        request=StaffMemberCreateSerializer,
        responses={201: StaffMemberSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request):
        serializer = StaffMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            staff = create_staff_member(
                request.user.operator_staff.operator, actor=request.user, **serializer.validated_data
            )
        except StaffManagementError as exc:
            return _service_error_response(exc)
        return Response(StaffMemberSerializer(staff).data, status=status.HTTP_201_CREATED)


class StaffDetailView(APIView):
    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsOperatorOwnerOrManager()]
        return [IsOperatorOwnerOrManager(), IsApprovedOperator()]

    def _get_staff(self, request, public_id):
        return generics.get_object_or_404(
            OperatorStaff.objects.select_related("user"),
            public_id=public_id,
            operator=request.user.operator_staff.operator,
        )

    @extend_schema(responses=StaffMemberSerializer)
    def get(self, request, public_id):
        return Response(StaffMemberSerializer(self._get_staff(request, public_id)).data)

    @extend_schema(
        request=StaffMemberUpdateSerializer,
        responses={200: StaffMemberSerializer, 400: ErrorResponseSerializer},
    )
    def patch(self, request, public_id):
        staff = self._get_staff(request, public_id)
        serializer = StaffMemberUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            staff = update_staff_member(staff, actor=request.user, **serializer.validated_data)
        except StaffManagementError as exc:
            return _service_error_response(exc)
        return Response(StaffMemberSerializer(staff).data)
