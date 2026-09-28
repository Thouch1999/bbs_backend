from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.boarding import services as boarding_services
from apps.boarding.serializers import (
    MarkBoardingResponseSerializer,
    MarkBoardingSerializer,
    OperatorManifestRowSerializer,
)
from apps.core.mixins import OperatorScopedViewSetMixin
from apps.core.permissions import IsApprovedOperatorForWrites, IsOperatorOwnerOrManager, IsOperatorStaff
from apps.core.serializers import ErrorResponseSerializer
from apps.fleet.models import Seat
from apps.notifications import services as notification_services
from apps.routes.models import RouteStop
from apps.routes.serializers import RouteStopSerializer

from . import services
from .models import Trip, TripSeat
from .serializers import (
    HoldResponseSerializer,
    HoldSeatsSerializer,
    OperatorTripQuerySerializer,
    OperatorTripSerializer,
    RecurringTripCreateSerializer,
    ReleaseResponseSerializer,
    ReleaseSeatsSerializer,
    TripCreateSerializer,
    TripSeatSerializer,
    TripSerializer,
    TripStatusPreviewSerializer,
    TripStatusUpdateSerializer,
)


def _caller_identity(request):
    """Authenticated user, or the guest's session key (created if needed)."""
    if request.user and request.user.is_authenticated:
        return request.user, None
    if not request.session.session_key:
        request.session.create()
    return None, request.session.session_key


def _search_cache_key(params: dict) -> str:
    ordered = sorted(params.items())
    version = services.search_cache_version()
    return f"trip-search:v{version}:" + "&".join(f"{k}={v}" for k, v in ordered)


class TripSearchView(generics.ListAPIView):
    """Public trip search: ?origin=<city_public_id>&destination=<city_public_id>&date=YYYY-MM-DD."""

    serializer_class = TripSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        date_str = self.request.query_params.get("date")
        date = None
        if date_str:
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError as exc:
                raise serializers.ValidationError({"date": "Expected YYYY-MM-DD."}) from exc

        return services.search_trips(
            origin=self.request.query_params.get("origin"),
            destination=self.request.query_params.get("destination"),
            date=date,
            bus_type=self.request.query_params.get("bus_type"),
            operator=self.request.query_params.get("operator"),
        )

    def list(self, request, *args, **kwargs):
        cache_key = _search_cache_key(request.query_params.dict())
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=settings.TRIP_SEARCH_CACHE_TTL_SECONDS)
        return response


class TripDetailView(generics.RetrieveAPIView):
    queryset = Trip.objects.select_related(
        "route", "route__operator", "route__origin_city", "route__destination_city", "bus", "bus__seat_layout"
    )
    serializer_class = TripSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "public_id"


class TripSeatMapView(generics.ListAPIView):
    serializer_class = TripSeatSerializer
    permission_classes = [permissions.AllowAny]
    # A full bus (30-45+ seats) must render as one seat map, not be
    # silently truncated by the global DEFAULT_PAGINATION_CLASS page_size.
    pagination_class = None

    def get_queryset(self):
        return TripSeat.objects.filter(trip__public_id=self.kwargs["public_id"]).select_related("seat")


class TripStopsView(generics.ListAPIView):
    """Public: a trip's boarding/drop stop choices, in route order.

    RouteStopViewSet (apps.routes.views) is operator-scoped and 500s for an
    anonymous caller, so passengers need this separate read-only endpoint.
    """

    serializer_class = RouteStopSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        trip = generics.get_object_or_404(Trip, public_id=self.kwargs["public_id"])
        return RouteStop.objects.filter(route_id=trip.route_id).select_related("stop", "stop__city")


class HoldSeatsView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=HoldSeatsSerializer, responses=HoldResponseSerializer)
    def post(self, request, public_id):
        trip = generics.get_object_or_404(Trip.objects.select_related("bus"), public_id=public_id)
        serializer = HoldSeatsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        seat_public_ids = serializer.validated_data["seat_ids"]

        try:
            services.ensure_trip_bookable(trip)
        except services.TripNotBookableError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        except services.TripSeatLayoutDesyncedError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)

        seat_map = dict(
            Seat.objects.filter(
                public_id__in=seat_public_ids, seat_layout=trip.bus.seat_layout_id
            ).values_list("public_id", "id")
        )
        missing = set(seat_public_ids) - set(seat_map.keys())
        if missing:
            return Response(
                {"error": {"code": "seats_unavailable", "message": f"Unknown seats: {missing}"}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, session_key = _caller_identity(request)
        try:
            token = services.hold_seats(
                trip.id, list(seat_map.values()), user=user, session_key=session_key
            )
        except (services.TripLockedError, services.SeatsUnavailableError) as exc:
            return Response(
                {"error": {"code": exc.code, "message": str(exc)}}, status=status.HTTP_409_CONFLICT
            )

        payload = services.verify_hold_token(token)
        data = {
            "hold_token": token,
            "held_until": payload["held_until"],
            "seat_ids": seat_public_ids,
        }
        return Response(HoldResponseSerializer(data).data, status=status.HTTP_201_CREATED)


class ReleaseSeatsView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=ReleaseSeatsSerializer, responses=ReleaseResponseSerializer)
    def post(self, request, public_id):
        trip = generics.get_object_or_404(Trip, public_id=public_id)
        serializer = ReleaseSeatsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        seat_public_ids = serializer.validated_data["seat_ids"]

        seat_ids = list(
            Seat.objects.filter(public_id__in=seat_public_ids).values_list("id", flat=True)
        )
        user, session_key = _caller_identity(request)
        released = services.release_seats(trip.id, seat_ids, user=user, session_key=session_key)
        return Response({"released": released})


def _service_error_response(exc, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    error = {"code": exc.code, "message": str(exc)}
    conflicts = getattr(exc, "conflicts", None)
    if conflicts:
        error["details"] = {"conflicting_trip_ids": [str(t.public_id) for t in conflicts]}
    return Response({"error": error}, status=http_status)


@extend_schema_view(
    list=extend_schema(parameters=[OperatorTripQuerySerializer]),
    create=extend_schema(request=TripCreateSerializer, responses={201: OperatorTripSerializer}),
    update=extend_schema(request=TripCreateSerializer, responses=OperatorTripSerializer),
    partial_update=extend_schema(request=TripCreateSerializer, responses=OperatorTripSerializer),
)
class TripViewSet(OperatorScopedViewSetMixin, viewsets.ModelViewSet):
    """
    Operator trips. Any staff member can read them (a counter agent picks a
    trip to sell, a conductor picks today's trip to scan); only an
    owner/manager of an approved operator can schedule/edit/cancel.
    """

    queryset = Trip.objects.select_related(
        "route", "route__origin_city", "route__destination_city", "route__operator",
        "bus", "bus__seat_layout", "conductor__user",
    )
    serializer_class = OperatorTripSerializer
    lookup_field = "public_id"
    operator_lookup = "route__operator"

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS and self.action in ("list", "retrieve"):
            return [IsOperatorStaff()]
        return [IsOperatorOwnerOrManager(), IsApprovedOperatorForWrites()]

    def get_queryset(self):
        qs = services.with_operator_stats(super().get_queryset())
        if self.action != "list":
            return qs

        query = OperatorTripQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        if params.get("date_from"):
            qs = qs.filter(departure_at__gte=services.local_day_bounds(params["date_from"])[0])
        if params.get("date_to"):
            qs = qs.filter(departure_at__lt=services.local_day_bounds(params["date_to"])[1])
        if params.get("route"):
            qs = qs.filter(route__public_id=params["route"])
        if params.get("bus"):
            qs = qs.filter(bus__public_id=params["bus"])
        if params.get("status"):
            qs = qs.filter(status__in=[s.strip() for s in params["status"].split(",") if s.strip()])
        if params.get("mine"):
            qs = qs.filter(conductor__user=self.request.user)
        if params.get("unassigned"):
            qs = qs.filter(Q(driver_name="") | Q(conductor__isnull=True))
        return qs.order_by("departure_at")

    def _annotated(self, trip):
        return self.get_queryset().get(pk=trip.pk)

    def create(self, request, *args, **kwargs):
        serializer = TripCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        try:
            trip = services.create_trip(**serializer.validated_data)
        except services.TripScheduleError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        return Response(OperatorTripSerializer(self._annotated(trip)).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        trip = self.get_object()
        partial = kwargs.pop("partial", False)
        serializer = TripCreateSerializer(
            data=request.data, partial=partial, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        try:
            trip = services.update_trip(trip, **serializer.validated_data)
        except services.TripLockedError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        except services.TripScheduleError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        return Response(OperatorTripSerializer(self._annotated(trip)).data)

    @extend_schema(
        request=RecurringTripCreateSerializer,
        responses={201: OperatorTripSerializer(many=True), 409: ErrorResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def recurring(self, request):
        serializer = RecurringTripCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        try:
            trips = services.create_recurring_trips(**serializer.validated_data)
        except services.TripScheduleError as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        created = self.get_queryset().filter(pk__in=[t.pk for t in trips]).order_by("departure_at")
        return Response(OperatorTripSerializer(created, many=True).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=TripStatusUpdateSerializer,
        responses={200: OperatorTripSerializer, 400: ErrorResponseSerializer},
    )
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, public_id=None):
        trip = self.get_object()
        serializer = TripStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            trip = services.set_trip_status(
                trip,
                status=serializer.validated_data["status"],
                delay_minutes=serializer.validated_data.get("delay_minutes"),
                cancellation_reason=serializer.validated_data.get("cancellation_reason", ""),
                actor=request.user,
            )
        except services.TripStatusError as exc:
            return _service_error_response(exc)
        return Response(OperatorTripSerializer(self._annotated(trip)).data)

    @extend_schema(
        parameters=[TripStatusUpdateSerializer],
        responses={200: TripStatusPreviewSerializer, 400: ErrorResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="status-preview")
    def status_preview(self, request, public_id=None):
        """Read-only: who a status change would notify and the exact message
        text, for the confirmation dialog — never sends anything."""
        trip = self.get_object()
        serializer = TripStatusUpdateSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            services.check_status_transition(trip, data["status"])
        except services.TripStatusError as exc:
            return _service_error_response(exc)
        preview = notification_services.preview_trip_status_notifications(
            trip,
            status=data["status"],
            delay_minutes=data.get("delay_minutes"),
            cancellation_reason=data.get("cancellation_reason", ""),
        )
        return Response(TripStatusPreviewSerializer(preview).data)

    @extend_schema(responses=OperatorManifestRowSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None)
    def manifest(self, request, public_id=None):
        """Owner/manager manifest (O4): confirmed + pending-payment passengers
        with boarding point, payment and boarding status."""
        trip = self.get_object()
        rows = boarding_services.get_manifest(trip, include_pending=True)
        return Response(OperatorManifestRowSerializer(rows, many=True).data)

    @extend_schema(request=MarkBoardingSerializer, responses=MarkBoardingResponseSerializer)
    @action(detail=True, methods=["post"])
    def boarding(self, request, public_id=None):
        """Bulk "mark as boarded"/"no-show" from the manifest."""
        trip = self.get_object()
        serializer = MarkBoardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = boarding_services.mark_passengers_boarding(
            trip,
            serializer.validated_data["passenger_ids"],
            status=serializer.validated_data["status"],
            marked_by=request.user,
        )
        return Response({"updated": updated})
