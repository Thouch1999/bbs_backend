from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, permissions, serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.mixins import OperatorScopedViewSetMixin
from apps.core.permissions import IsApprovedOperator, IsOperatorOwnerOrManager
from apps.core.serializers import ErrorResponseSerializer

from . import services
from .models import City, Route, RouteStop, Stop
from .serializers import (
    CitySerializer,
    RouteSerializer,
    RouteStopListSerializer,
    RouteStopSerializer,
    StopSerializer,
)


class CityViewSet(viewsets.ReadOnlyModelViewSet):
    """Public: cities list, searchable by ?search= against either name."""

    queryset = City.objects.filter(is_active=True).order_by("name_en")
    serializer_class = CitySerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(name_en__icontains=search) | Q(name_km__icontains=search))
        return qs


class StopViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """
    Public: stops, filterable by ?city=<public_id> and ?search=. An approved
    operator's owner/manager may also add a stop (their own depot or pickup
    point) — create only: stops are shared reference data across operators,
    so nobody but an admin edits or removes one another operator may use.
    """

    queryset = Stop.objects.filter(is_active=True).select_related("city")
    serializer_class = StopSerializer
    lookup_field = "public_id"

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.AllowAny()]
        return [IsOperatorOwnerOrManager(), IsApprovedOperator()]

    def get_queryset(self):
        qs = super().get_queryset()
        city = self.request.query_params.get("city")
        if city:
            qs = qs.filter(city__public_id=city)
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(name_en__icontains=search) | Q(name_km__icontains=search))
        return qs


class RouteViewSet(OperatorScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Route.objects.select_related("origin_city", "destination_city").annotate(
        stops_count=Count("route_stops", distinct=True)
    )
    serializer_class = RouteSerializer
    lookup_field = "public_id"


class RouteStopViewSet(OperatorScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = RouteStop.objects.select_related("stop", "stop__city", "route")
    serializer_class = RouteStopSerializer
    lookup_field = "public_id"
    operator_lookup = "route__operator"
    # A route's stop list is short and always wanted whole.
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.filter(route__public_id=self.kwargs["route_public_id"])

    def perform_create(self, serializer):
        route = generics.get_object_or_404(
            Route, public_id=self.kwargs["route_public_id"], operator=self._operator()
        )
        sequence = serializer.validated_data.get("sequence")
        if RouteStop.objects.filter(route=route, sequence=sequence).exists():
            raise serializers.ValidationError({"sequence": "This route already has a stop at this sequence."})
        serializer.save(route=route)


class RouteStopListReplaceView(APIView):
    """PUT the route's whole ordered stop list in one call — what a
    drag-to-reorder editor needs, since reordering row by row through
    RouteStopViewSet trips UNIQUE(route, sequence) on the first swap."""

    permission_classes = [IsOperatorOwnerOrManager, IsApprovedOperator]

    @extend_schema(
        request=RouteStopListSerializer,
        responses={
            200: RouteStopSerializer(many=True),
            400: ErrorResponseSerializer,
            409: ErrorResponseSerializer,
        },
    )
    def put(self, request, route_public_id):
        route = generics.get_object_or_404(
            Route, public_id=route_public_id, operator=request.user.operator_staff.operator
        )
        serializer = RouteStopListSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            route_stops = services.set_route_stops(route, serializer.validated_data["stops"])
        except services.RouteStopsError as exc:
            error = {"code": exc.code, "message": str(exc)}
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        saved = RouteStop.objects.filter(pk__in=[rs.pk for rs in route_stops]).select_related("stop__city")
        return Response(RouteStopSerializer(saved.order_by("sequence"), many=True).data)
