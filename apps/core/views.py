from datetime import datetime, time, timedelta
from decimal import Decimal

import redis
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.models import Q
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AuditLog
from .permissions import IsAdmin
from .serializers import AuditLogQuerySerializer, AuditLogSerializer
from .utils import to_khr


class HealthzView(View):
    """Liveness: this process is up and can handle a request at all. Never
    touches the DB/Redis — a slow dependency must not make Kubernetes/
    docker-compose kill and restart an otherwise-healthy process."""

    def get(self, request):
        return JsonResponse({"status": "ok"})


class ReadyzView(View):
    """Readiness: this process can actually serve traffic right now — the DB
    and Redis it depends on are both reachable. Used to gate a load
    balancer/orchestrator sending traffic here, not to trigger a restart."""

    def get(self, request):
        checks = {"database": self._check_database(), "redis": self._check_redis()}
        healthy = all(checks.values())
        body = {"status": "ok" if healthy else "unavailable", "checks": checks}
        return JsonResponse(body, status=200 if healthy else 503)

    def _check_database(self) -> bool:
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except OperationalError:
            return False

    def _check_redis(self) -> bool:
        try:
            probe_key = "readyz:probe"
            cache.set(probe_key, "1", timeout=5)
            return cache.get(probe_key) == "1"
        except redis.exceptions.RedisError:
            return False


class PublicConfigSerializer(serializers.Serializer):
    khr_per_usd = serializers.DecimalField(max_digits=10, decimal_places=2)
    seat_hold_minutes = serializers.IntegerField()
    counter_payment_deadline_minutes = serializers.IntegerField()
    booking_fee_usd = serializers.DecimalField(max_digits=8, decimal_places=2)
    booking_fee_khr = serializers.DecimalField(max_digits=12, decimal_places=0)


class PublicConfigView(APIView):
    """Read-only platform settings the frontend needs to render hints (e.g.
    the operator fare form's KHR<->USD auto-conversion) without hardcoding
    values that would silently drift from the backend's .env."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=PublicConfigSerializer)
    def get(self, request):
        fee_usd = Decimal(str(settings.BOOKING_FEE_USD))
        data = {
            "khr_per_usd": Decimal(str(settings.KHR_PER_USD)),
            "seat_hold_minutes": settings.SEAT_HOLD_MINUTES,
            "counter_payment_deadline_minutes": settings.COUNTER_PAYMENT_DEADLINE_MINUTES,
            # Same conversion bookings.services._booking_fee applies, so the
            # counter screen's pre-booking total matches the booking's.
            "booking_fee_usd": fee_usd,
            "booking_fee_khr": to_khr(fee_usd),
        }
        return Response(PublicConfigSerializer(data).data)


@extend_schema(parameters=[AuditLogQuerySerializer])
class AdminAuditLogListView(generics.ListAPIView):
    """System audit log (SRS 3.3/7.18), newest first."""

    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        query = AuditLogQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")
        for field in ("action", "target_model", "target_id"):
            if params.get(field):
                qs = qs.filter(**{field: params[field]})
        if params.get("booking"):
            from apps.payments.models import Refund

            refund_ids = [
                str(pid)
                for pid in Refund.objects.filter(payment__booking__public_id=params["booking"]).values_list(
                    "public_id", flat=True
                )
            ]
            qs = qs.filter(
                Q(target_model="Booking", target_id=str(params["booking"]))
                | Q(target_model="Refund", target_id__in=refund_ids)
            )
        tz = timezone.get_current_timezone()
        if params.get("date_from"):
            qs = qs.filter(
                created_at__gte=timezone.make_aware(datetime.combine(params["date_from"], time.min), tz)
            )
        if params.get("date_to"):
            end = timezone.make_aware(datetime.combine(params["date_to"], time.min), tz) + timedelta(days=1)
            qs = qs.filter(created_at__lt=end)
        return qs
