from datetime import datetime, time, timedelta

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookings.models import Booking
from apps.bookings.services import estimate_subtotal
from apps.core.permissions import IsAdmin, IsCounterAgent
from apps.core.serializers import ErrorResponseSerializer

from . import services
from .gateways import GatewayNotImplementedError, InvalidSignatureError
from .models import OperatorPayout, Payment, PromoCode
from .serializers import (
    AdminPaymentDetailSerializer,
    AdminPaymentQuerySerializer,
    AdminPaymentRowSerializer,
    AdminPromoCodeSerializer,
    InitiatePaymentResponseSerializer,
    InitiatePaymentSerializer,
    OperatorBalanceSerializer,
    OperatorPayoutSerializer,
    PaymentSerializer,
    PromoValidateRequestSerializer,
    PromoValidateResponseSerializer,
    ReconciliationSummarySerializer,
    RecordPayoutSerializer,
    RefundRequestSerializer,
    RefundSerializer,
)


def _error_response(exc, status_code=status.HTTP_400_BAD_REQUEST) -> Response:
    return Response({"error": {"code": exc.code, "message": str(exc)}}, status=status_code)


class InitiatePaymentView(APIView):
    """Booking (and paying for it) requires a passenger account — see
    CreateBookingView."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=InitiatePaymentSerializer,
        responses={
            201: InitiatePaymentResponseSerializer,
            400: ErrorResponseSerializer,
            501: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                "Pay with ABA PayWay",
                value={"booking_id": "3fae1d2e-7c3a-4a1a-9b8e-7b1e2a6f9c11", "provider": "aba_payway"},
                request_only=True,
            ),
            OpenApiExample(
                "Pay at counter (FR4.3)",
                value={"booking_id": "3fae1d2e-7c3a-4a1a-9b8e-7b1e2a6f9c11", "provider": "counter"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.validated_data["booking_id"]

        try:
            payment, gateway_payload = services.initiate_payment(
                booking, serializer.validated_data["provider"]
            )
        except services.PaymentStateError as exc:
            return _error_response(exc)
        except GatewayNotImplementedError as exc:
            return _error_response(exc, status.HTTP_501_NOT_IMPLEMENTED)

        return Response(
            {"payment": PaymentSerializer(payment).data, "gateway": gateway_payload},
            status=status.HTTP_201_CREATED,
        )


class WebhookView(APIView):
    """POST /payments/webhook/{provider}/ — called by the gateway itself, not our clients."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)  # external gateway callback, not part of our client-facing contract
    def post(self, request, provider):
        try:
            payment = services.process_webhook(provider, request)
        except InvalidSignatureError:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response(status=status.HTTP_404_NOT_FOUND)  # unknown provider

        return Response({"received": True, "payment": str(payment.public_id) if payment else None})


class MarkCounterPaidView(APIView):
    """Staff confirms a pay-at-counter payment was received in cash (FR4.3)."""

    permission_classes = [IsCounterAgent]

    @extend_schema(request=None, responses={200: PaymentSerializer, 400: ErrorResponseSerializer})
    def post(self, request, public_id):
        payment = generics.get_object_or_404(Payment, public_id=public_id)
        operator_id = payment.booking.trip.route.operator_id
        if operator_id != request.user.operator_staff.operator_id:
            error = {"code": "cross_operator", "message": "This payment belongs to a different operator."}
            return Response({"error": error}, status=status.HTTP_403_FORBIDDEN)

        try:
            payment = services.mark_counter_payment_paid(payment)
        except services.PaymentStateError as exc:
            return _error_response(exc)
        return Response(PaymentSerializer(payment).data)


class CreateRefundView(APIView):
    """Issuing a refund is admin-gated — it moves real money."""

    permission_classes = [IsAdmin]

    @extend_schema(
        request=RefundRequestSerializer,
        responses={
            201: RefundSerializer,
            400: ErrorResponseSerializer,
            501: ErrorResponseSerializer,
        },
        examples=[
            OpenApiExample(
                "Partial goodwill refund",
                value={"amount": "5.00", "reason": "Bus broke down"},
                request_only=True,
            )
        ],
    )
    def post(self, request, public_id):
        payment = generics.get_object_or_404(Payment, public_id=public_id)
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        reason = serializer.validated_data.get("reason", "")
        try:
            refund = services.create_refund(payment, amount, reason=reason, actor=request.user)
        except services.PaymentStateError as exc:
            return _error_response(exc)
        except GatewayNotImplementedError as exc:
            return _error_response(exc, status.HTTP_501_NOT_IMPLEMENTED)

        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class PromoValidateView(APIView):
    """Lets the checkout screen preview a promo code's discount before booking (SRS W6)."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=PromoValidateRequestSerializer,
        responses={200: PromoValidateResponseSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request):
        serializer = PromoValidateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        trip = data["trip_id"]
        subtotal = estimate_subtotal(trip, data["currency"], data["num_seats"])

        try:
            discount = services.validate_promo_code(
                data["code"],
                subtotal=subtotal,
                trip=trip,
                user=request.user if request.user.is_authenticated else None,
                contact_phone=data.get("contact_phone", ""),
            )
        except services.PromoCodeError as exc:
            return _error_response(exc)

        return Response({"subtotal": subtotal, "discount_amount": discount})



# --- Admin (Step 16) ---------------------------------------------------------
def _admin_payments():
    return Payment.objects.select_related("booking__trip__route__operator").prefetch_related("refunds")


@extend_schema(parameters=[AdminPaymentQuerySerializer])
class AdminPaymentListView(generics.ListAPIView):
    """A3 transaction table, filterable; status=mismatch uses the same SQL
    definition as the summary count (services.mismatch_q)."""

    serializer_class = AdminPaymentRowSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        query = AdminPaymentQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = _admin_payments().order_by("-created_at")
        tz = timezone.get_current_timezone()
        if params.get("date_from"):
            start = timezone.make_aware(datetime.combine(params["date_from"], time.min), tz)
            qs = qs.filter(created_at__gte=start)
        if params.get("date_to"):
            end = timezone.make_aware(datetime.combine(params["date_to"], time.min), tz) + timedelta(days=1)
            qs = qs.filter(created_at__lt=end)
        if params.get("provider"):
            qs = qs.filter(provider=params["provider"])
        if params.get("operator"):
            qs = qs.filter(booking__trip__route__operator__public_id=params["operator"])
        if params.get("currency"):
            qs = qs.filter(currency=params["currency"])
        status_filter = params.get("status")
        if status_filter == "mismatch":
            qs = qs.filter(services.mismatch_q())
        elif status_filter == "paid":
            qs = qs.filter(status=Payment.Status.SUCCEEDED).exclude(services.mismatch_q())
        elif status_filter in ("pending", "failed"):
            qs = qs.filter(status=status_filter)
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(Q(booking__pnr__iexact=term) | Q(provider_txn_id__icontains=term))
        return qs


class AdminPaymentDetailView(generics.RetrieveAPIView):
    serializer_class = AdminPaymentDetailSerializer
    permission_classes = [IsAdmin]
    lookup_field = "public_id"

    def get_queryset(self):
        return _admin_payments().prefetch_related("webhook_logs")


class AdminReconciliationSummaryView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=ReconciliationSummarySerializer)
    def get(self, request):
        return Response(ReconciliationSummarySerializer(services.reconciliation_summary()).data)


class AdminOperatorBalancesView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=OperatorBalanceSerializer(many=True))
    def get(self, request):
        return Response(OperatorBalanceSerializer(services.operator_balances(), many=True).data)


class AdminPayoutListCreateView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=OperatorPayoutSerializer(many=True))
    def get(self, request):
        payouts = OperatorPayout.objects.select_related("operator", "paid_by")[:200]
        return Response(OperatorPayoutSerializer(payouts, many=True).data)

    @extend_schema(
        request=RecordPayoutSerializer,
        responses={201: OperatorPayoutSerializer, 400: ErrorResponseSerializer},
    )
    def post(self, request):
        serializer = RecordPayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        operator = data.pop("operator")
        try:
            payout = services.record_payout(operator, actor=request.user, **data)
        except services.PayoutError as exc:
            return _error_response(exc)
        return Response(OperatorPayoutSerializer(payout).data, status=status.HTTP_201_CREATED)


class AdminPromoCodeViewSet(viewsets.ModelViewSet):
    """A4 promo codes. times_used counts non-cancelled bookings that used the
    code — the same rule validate_promo_code enforces max_uses with."""

    serializer_class = AdminPromoCodeSerializer
    permission_classes = [IsAdmin]
    lookup_field = "public_id"

    def get_queryset(self):
        used = (
            Booking.objects.filter(promo_code=OuterRef("code"))
            .exclude(status=Booking.Status.CANCELLED)
            .order_by()
            .values("promo_code")
            .annotate(n=Count("id"))
            .values("n")[:1]
        )
        return (
            PromoCode.objects.select_related("applicable_operator", "applicable_route")
            .annotate(times_used=Coalesce(Subquery(used, output_field=IntegerField()), Value(0)))
            .order_by("-created_at")
        )

    def perform_destroy(self, instance):
        # A code already on bookings stays for the audit trail — deactivate it.
        if Booking.objects.filter(promo_code=instance.code).exists():
            raise serializers.ValidationError({"code": "This code has been used — deactivate it instead."})
        instance.delete()
