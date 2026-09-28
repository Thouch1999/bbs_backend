from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.mixins import OperatorScopedViewSetMixin
from apps.core.serializers import ErrorResponseSerializer

from .models import Bus, SeatLayout
from .serializers import (
    BusSerializer,
    SeatLayoutBuildSerializer,
    SeatLayoutSeatsSerializer,
    SeatLayoutSerializer,
    SeatSerializer,
)
from .services import (
    BusInUseError,
    SeatLayoutError,
    SeatLayoutInUseError,
    build_seat_layout,
    replace_layout_seats,
    update_bus,
)


def _service_error_response(exc) -> Response:
    in_use = isinstance(exc, (SeatLayoutInUseError, BusInUseError))
    http_status = status.HTTP_409_CONFLICT if in_use else status.HTTP_400_BAD_REQUEST
    return Response({"error": {"code": exc.code, "message": str(exc)}}, status=http_status)


class SeatLayoutViewSet(OperatorScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = SeatLayout.objects.prefetch_related("seats").order_by("name")
    serializer_class = SeatLayoutSerializer
    lookup_field = "public_id"

    @extend_schema(
        request=SeatLayoutBuildSerializer,
        responses={200: SeatSerializer(many=True), 409: ErrorResponseSerializer},
    )
    @action(detail=True, methods=["post"])
    def build(self, request, public_id=None):
        seat_layout = self.get_object()
        serializer = SeatLayoutBuildSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            seats = build_seat_layout(seat_layout, **serializer.validated_data)
        except SeatLayoutError as exc:
            return _service_error_response(exc)
        return Response(SeatSerializer(seats, many=True).data)

    @extend_schema(
        request=SeatLayoutSeatsSerializer,
        responses={200: SeatLayoutSerializer, 400: ErrorResponseSerializer, 409: ErrorResponseSerializer},
    )
    @action(detail=True, methods=["put"])
    def seats(self, request, public_id=None):
        """Saves the visual layout builder's explicit seat list (positions,
        types, female-only) — replaces every seat on the layout."""
        seat_layout = self.get_object()
        serializer = SeatLayoutSeatsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            replace_layout_seats(seat_layout, **serializer.validated_data)
        except SeatLayoutError as exc:
            return _service_error_response(exc)
        seat_layout = self.get_queryset().get(pk=seat_layout.pk)
        return Response(SeatLayoutSerializer(seat_layout).data)


class BusViewSet(OperatorScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Bus.objects.select_related("seat_layout").prefetch_related("seat_layout__seats")
    serializer_class = BusSerializer
    lookup_field = "public_id"

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            update_bus(instance, **serializer.validated_data)
        except SeatLayoutError as exc:
            return _service_error_response(exc)
        instance.refresh_from_db()
        return Response(self.get_serializer(instance).data)
