from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsConductor
from apps.core.serializers import ErrorResponseSerializer
from apps.trips.models import Trip

from . import services
from .serializers import (
    BoardByPnrSerializer,
    ConductorManifestRowSerializer,
    OfflineSyncResultSerializer,
    OfflineSyncSerializer,
    ValidateQrResponseSerializer,
    ValidateQrSerializer,
)


def _service_error_response(exc, http_status=status.HTTP_400_BAD_REQUEST) -> Response:
    error = {"code": exc.code, "message": str(exc)}
    first_scanned_at = getattr(exc, "first_scanned_at", None)
    if first_scanned_at:
        # The S2 "already scanned" panel shows when the first scan happened.
        error["details"] = {"first_scanned_at": first_scanned_at.isoformat()}
    return Response({"error": error}, status=http_status)


def _get_operator_trip_or_403(request, public_id):
    """A conductor may only scan/view trips run by their own operator."""
    trip = get_object_or_404(Trip.objects.select_related("route"), public_id=public_id)
    if trip.route.operator_id != request.user.operator_staff.operator_id:
        return trip, Response(
            {"error": {"code": "cross_operator", "message": "This trip belongs to a different operator."}},
            status=status.HTTP_403_FORBIDDEN,
        )
    return trip, None


def _boarding_response(records):
    return Response(
        ValidateQrResponseSerializer({"pnr": records[0].booking.pnr, "boarded": records}).data,
        status=status.HTTP_200_OK,
    )


_BOARDING_ERRORS = {
    400: ErrorResponseSerializer,
    404: ErrorResponseSerializer,
    409: ErrorResponseSerializer,
}


class ValidateQrView(APIView):
    permission_classes = [IsConductor]

    @extend_schema(
        request=ValidateQrSerializer, responses={200: ValidateQrResponseSerializer, **_BOARDING_ERRORS}
    )
    def post(self, request, public_id):
        trip, error_response = _get_operator_trip_or_403(request, public_id)
        if error_response is not None:
            return error_response

        serializer = ValidateQrSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            records = services.validate_qr_and_board(
                serializer.validated_data["qr_token"], trip.id, scanned_by=request.user
            )
        except services.InvalidQrTokenError as exc:
            return _service_error_response(exc, status.HTTP_400_BAD_REQUEST)
        except (
            services.BookingNotConfirmedError,
            services.WrongTripError,
            services.AlreadyBoardedError,
        ) as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        return _boarding_response(records)


class BoardByPnrView(APIView):
    """Manual PNR entry — the fallback when the camera or the passenger's screen fails."""

    permission_classes = [IsConductor]

    @extend_schema(
        request=BoardByPnrSerializer, responses={200: ValidateQrResponseSerializer, **_BOARDING_ERRORS}
    )
    def post(self, request, public_id):
        trip, error_response = _get_operator_trip_or_403(request, public_id)
        if error_response is not None:
            return error_response
        serializer = BoardByPnrSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            records = services.board_by_pnr(serializer.validated_data["pnr"], trip, scanned_by=request.user)
        except services.BookingNotFoundError as exc:
            return _service_error_response(exc, status.HTTP_404_NOT_FOUND)
        except (
            services.BookingNotConfirmedError,
            services.WrongTripError,
            services.AlreadyBoardedError,
        ) as exc:
            return _service_error_response(exc, status.HTTP_409_CONFLICT)
        return _boarding_response(records)


class OfflineSyncView(APIView):
    """Uploads scans queued while offline; idempotent (see services.sync_offline_scans)."""

    permission_classes = [IsConductor]

    @extend_schema(request=OfflineSyncSerializer, responses=OfflineSyncResultSerializer(many=True))
    def post(self, request, public_id):
        trip, error_response = _get_operator_trip_or_403(request, public_id)
        if error_response is not None:
            return error_response
        serializer = OfflineSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = services.sync_offline_scans(
            trip, serializer.validated_data["scans"], scanned_by=request.user
        )
        return Response(OfflineSyncResultSerializer(results, many=True).data)


class ManifestView(APIView):
    permission_classes = [IsConductor]

    @extend_schema(responses=ConductorManifestRowSerializer(many=True))
    def get(self, request, public_id):
        trip, error_response = _get_operator_trip_or_403(request, public_id)
        if error_response is not None:
            return error_response

        manifest = services.get_manifest(trip)
        return Response(ConductorManifestRowSerializer(manifest, many=True).data)
