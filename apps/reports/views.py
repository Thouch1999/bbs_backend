import csv

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.boarding import services as boarding_services
from apps.core.permissions import IsAdmin, IsOperatorOwnerOrManager
from apps.trips.models import Trip

from . import services
from .manifest_pdf import generate_manifest_pdf
from .serializers import (
    AdminOverviewSerializer,
    CancellationSummarySerializer,
    DailySeriesPointSerializer,
    DateRangeQuerySerializer,
    ManifestExportQuerySerializer,
    OccupancyRowSerializer,
    OperatorDashboardSerializer,
    OperatorSummaryRowSerializer,
    PlatformTotalsSerializer,
    ReconciliationRowSerializer,
    RevenueQuerySerializer,
    RevenueRowSerializer,
    SystemStatusRowSerializer,
    TopRouteSerializer,
)


def _validated_query(serializer_class, request):
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _operator(request):
    return request.user.operator_staff.operator


class OperatorDashboardView(APIView):
    permission_classes = [IsOperatorOwnerOrManager]

    @extend_schema(responses=OperatorDashboardSerializer)
    def get(self, request):
        return Response(OperatorDashboardSerializer(services.operator_dashboard(_operator(request))).data)


class OperatorRevenueView(APIView):
    permission_classes = [IsOperatorOwnerOrManager]

    @extend_schema(parameters=[RevenueQuerySerializer], responses=RevenueRowSerializer(many=True))
    def get(self, request):
        query = _validated_query(RevenueQuerySerializer, request)
        rows = services.operator_revenue(
            _operator(request),
            date_from=query.get("date_from"),
            date_to=query.get("date_to"),
            group_by=query["group_by"],
        )
        return Response(RevenueRowSerializer(rows, many=True).data)


class OperatorOccupancyView(APIView):
    permission_classes = [IsOperatorOwnerOrManager]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=OccupancyRowSerializer(many=True))
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        rows = services.operator_occupancy(
            _operator(request), date_from=query.get("date_from"), date_to=query.get("date_to")
        )
        return Response(OccupancyRowSerializer(rows, many=True).data)


class OperatorCancellationSummaryView(APIView):
    permission_classes = [IsOperatorOwnerOrManager]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=CancellationSummarySerializer)
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        summary = services.operator_cancellation_summary(
            _operator(request), date_from=query.get("date_from"), date_to=query.get("date_to")
        )
        return Response(CancellationSummarySerializer(summary).data)


_MANIFEST_CSV_HEADER = [
    "Seat",
    "PNR",
    "Passenger",
    "Age",
    "Gender",
    "Phone",
    "Boarding point",
    "Payment",
    "Channel",
    "Boarded",
]


class OperatorManifestExportView(APIView):
    """FR3.2: passenger manifest export as CSV or PDF, for one of the operator's own trips."""

    permission_classes = [IsOperatorOwnerOrManager]

    @extend_schema(parameters=[ManifestExportQuerySerializer], responses={200: OpenApiTypes.BINARY})
    def get(self, request, public_id):
        trip = get_object_or_404(Trip.objects.select_related("route"), public_id=public_id)
        if trip.route.operator_id != _operator(request).id:
            error = {"code": "cross_operator", "message": "This trip belongs to a different operator."}
            return Response({"error": error}, status=status.HTTP_403_FORBIDDEN)

        query = _validated_query(ManifestExportQuerySerializer, request)
        manifest = boarding_services.get_manifest(trip, include_pending=True)

        if query["export_format"] == "pdf":
            pdf_bytes = generate_manifest_pdf(trip, manifest)
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="manifest-{trip.public_id}.pdf"'
            return response

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="manifest-{trip.public_id}.csv"'
        writer = csv.writer(response)
        writer.writerow(_MANIFEST_CSV_HEADER)
        for row in manifest:
            writer.writerow(
                [
                    row["seat_number"],
                    row["pnr"],
                    row["full_name"],
                    row["age"] if row["age"] is not None else "",
                    row["gender"],
                    row["phone"],
                    row["boarding_stop_name_en"],
                    row["payment_status"],
                    row["booking_channel"],
                    row["boarded"],
                ]
            )
        return response


class AdminPlatformTotalsView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=PlatformTotalsSerializer)
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        totals = services.admin_platform_totals(
            date_from=query.get("date_from"), date_to=query.get("date_to")
        )
        return Response(PlatformTotalsSerializer(totals).data)


class AdminTopRoutesView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=TopRouteSerializer(many=True))
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        limit = int(request.query_params.get("limit", 10))
        rows = services.admin_top_routes(
            date_from=query.get("date_from"), date_to=query.get("date_to"), limit=limit
        )
        return Response(TopRouteSerializer(rows, many=True).data)


class AdminReconciliationView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=ReconciliationRowSerializer(many=True))
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        rows = services.admin_reconciliation(date_from=query.get("date_from"), date_to=query.get("date_to"))
        return Response(ReconciliationRowSerializer(rows, many=True).data)


class AdminOverviewView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=AdminOverviewSerializer)
    def get(self, request):
        return Response(AdminOverviewSerializer(services.admin_overview()).data)


class AdminOperatorSummaryView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=OperatorSummaryRowSerializer(many=True))
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        rows = services.admin_operator_summary(date_from=query.get("date_from"), date_to=query.get("date_to"))
        return Response(OperatorSummaryRowSerializer(rows, many=True).data)


class AdminDailySeriesView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(parameters=[DateRangeQuerySerializer], responses=DailySeriesPointSerializer(many=True))
    def get(self, request):
        query = _validated_query(DateRangeQuerySerializer, request)
        rows = services.admin_daily_series(date_from=query.get("date_from"), date_to=query.get("date_to"))
        return Response(DailySeriesPointSerializer(rows, many=True).data)


class AdminSystemStatusView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=SystemStatusRowSerializer(many=True))
    def get(self, request):
        return Response(SystemStatusRowSerializer(services.system_status(), many=True).data)
