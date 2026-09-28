from decimal import Decimal

from rest_framework import serializers

from apps.bookings.models import Booking
from apps.operators.models import Operator
from apps.routes.models import Route
from apps.trips.models import Trip

from .models import OperatorPayout, Payment, PromoCode, Refund, WebhookLog


class InitiatePaymentSerializer(serializers.Serializer):
    booking_id = serializers.SlugRelatedField(slug_field="public_id", queryset=Booking.objects.all())
    provider = serializers.ChoiceField(choices=Payment.Provider.choices)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["public_id", "provider", "status", "amount", "currency", "provider_txn_id", "created_at"]
        read_only_fields = fields


class InitiatePaymentResponseSerializer(serializers.Serializer):
    payment = PaymentSerializer()
    gateway = serializers.DictField()


class RefundRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ["public_id", "amount", "reason", "status", "provider_refund_id", "created_at"]
        read_only_fields = fields


class PromoValidateRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)
    trip_id = serializers.SlugRelatedField(slug_field="public_id", queryset=Trip.objects.all())
    num_seats = serializers.IntegerField(min_value=1)
    currency = serializers.ChoiceField(choices=Booking.Currency.choices, default=Booking.Currency.USD)
    contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=16)


class PromoValidateResponseSerializer(serializers.Serializer):
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)



# --- Admin (Step 16) ---------------------------------------------------------
def _money(amount: Decimal) -> str:
    return str(Decimal(amount).quantize(Decimal("0.01")))


class AdminPaymentRowSerializer(serializers.ModelSerializer):
    """A3 table row. Expects the queryset built by AdminPaymentListView
    (booking/trip/route/operator joined, refunds prefetched)."""

    pnr = serializers.CharField(source="booking.pnr", read_only=True)
    booking_public_id = serializers.UUIDField(source="booking.public_id", read_only=True)
    booking_status = serializers.CharField(source="booking.status", read_only=True)
    operator_public_id = serializers.UUIDField(source="booking.trip.route.operator.public_id", read_only=True)
    operator_name = serializers.CharField(source="booking.trip.route.operator.name", read_only=True)
    refunded = serializers.SerializerMethodField()
    commission = serializers.SerializerMethodField()
    net_to_operator = serializers.SerializerMethodField()
    flags = serializers.SerializerMethodField()
    reconciliation_status = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "public_id",
            "provider",
            "provider_txn_id",
            "status",
            "amount",
            "currency",
            "pnr",
            "booking_public_id",
            "booking_status",
            "operator_public_id",
            "operator_name",
            "refunded",
            "commission",
            "net_to_operator",
            "flags",
            "reconciliation_status",
            "created_at",
        ]
        read_only_fields = fields

    def _settlement(self, payment):
        cache = self.context.setdefault("_settlements", {})
        if payment.pk not in cache:
            from .services import payment_settlement

            cache[payment.pk] = payment_settlement(payment)
        return cache[payment.pk]

    # Money leaves as a decimal string, like every DecimalField in the API:
    # a SerializerMethodField returning Decimal would reach DRF's JSON encoder
    # as-is and be rendered as a float (CLAUDE.md: money is never a float).
    def get_refunded(self, payment) -> str:
        return _money(self._settlement(payment)["refunded"])

    def get_commission(self, payment) -> str:
        return _money(self._settlement(payment)["commission"])

    def get_net_to_operator(self, payment) -> str:
        return _money(self._settlement(payment)["net_to_operator"])

    def get_flags(self, payment) -> list[str]:
        from .services import payment_flags

        return payment_flags(payment)

    def get_reconciliation_status(self, payment) -> str:
        from .services import payment_flags, reconciliation_status

        return reconciliation_status(payment, payment_flags(payment))


class WebhookLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookLog
        fields = ["public_id", "provider", "signature_valid", "note", "raw_body", "created_at"]
        read_only_fields = fields


class AdminPaymentDetailSerializer(AdminPaymentRowSerializer):
    """The "investigate" slide-over: the platform's booking record next to
    what the gateway reported, so differences can be compared field by field."""

    booking_total_amount = serializers.DecimalField(
        source="booking.total_amount", max_digits=12, decimal_places=2, read_only=True
    )
    booking_currency = serializers.CharField(source="booking.currency", read_only=True)
    booking_refund_amount = serializers.DecimalField(
        source="booking.refund_amount", max_digits=12, decimal_places=2, read_only=True, allow_null=True
    )
    booking_rescheduled_at = serializers.DateTimeField(source="booking.rescheduled_at", read_only=True)
    raw_payload = serializers.JSONField(read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)
    webhook_logs = WebhookLogSerializer(many=True, read_only=True)
    refundable = serializers.SerializerMethodField()

    class Meta(AdminPaymentRowSerializer.Meta):
        fields = AdminPaymentRowSerializer.Meta.fields + [
            "booking_total_amount",
            "booking_currency",
            "booking_refund_amount",
            "booking_rescheduled_at",
            "raw_payload",
            "refunds",
            "webhook_logs",
            "refundable",
        ]
        read_only_fields = fields

    def get_refundable(self, payment) -> str:
        from .services import refundable_balance

        if payment.status != Payment.Status.SUCCEEDED:
            return _money(Decimal(0))
        return _money(refundable_balance(payment))


class AdminPaymentQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    provider = serializers.ChoiceField(choices=Payment.Provider.choices, required=False)
    operator = serializers.UUIDField(required=False)
    currency = serializers.ChoiceField(choices=Booking.Currency.choices, required=False)
    status = serializers.ChoiceField(choices=["paid", "pending", "failed", "mismatch"], required=False)
    search = serializers.CharField(required=False, help_text="PNR or gateway transaction id.")


class CurrencyPairSerializer(serializers.Serializer):
    usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class ReconciliationSummarySerializer(serializers.Serializer):
    collected_today = CurrencyPairSerializer()
    paid_out = CurrencyPairSerializer()
    pending_payout = CurrencyPairSerializer()
    mismatch_count = serializers.IntegerField()


class OperatorBalanceSerializer(serializers.Serializer):
    operator_public_id = serializers.UUIDField()
    operator_name = serializers.CharField()
    currency = serializers.CharField()
    bookings_count = serializers.IntegerField()
    gross = serializers.DecimalField(max_digits=14, decimal_places=2)
    refunded = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission = serializers.DecimalField(max_digits=14, decimal_places=2)
    earned = serializers.DecimalField(max_digits=14, decimal_places=2)
    paid_out = serializers.DecimalField(max_digits=14, decimal_places=2)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2)
    last_paid_at = serializers.DateTimeField(allow_null=True)


class OperatorPayoutSerializer(serializers.ModelSerializer):
    operator_public_id = serializers.UUIDField(source="operator.public_id", read_only=True)
    operator_name = serializers.CharField(source="operator.name", read_only=True)
    paid_by_name = serializers.CharField(source="paid_by.full_name", read_only=True, default="")

    class Meta:
        model = OperatorPayout
        fields = [
            "public_id",
            "operator_public_id",
            "operator_name",
            "currency",
            "amount",
            "period_start",
            "period_end",
            "reference",
            "note",
            "paid_at",
            "paid_by_name",
        ]
        read_only_fields = fields


class RecordPayoutSerializer(serializers.Serializer):
    operator_id = serializers.SlugRelatedField(
        source="operator", slug_field="public_id", queryset=Operator.objects.all()
    )
    currency = serializers.ChoiceField(choices=Booking.Currency.choices)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class AdminPromoCodeSerializer(serializers.ModelSerializer):
    applicable_operator_id = serializers.SlugRelatedField(
        source="applicable_operator",
        slug_field="public_id",
        queryset=Operator.objects.all(),
        required=False,
        allow_null=True,
    )
    applicable_route_id = serializers.SlugRelatedField(
        source="applicable_route",
        slug_field="public_id",
        queryset=Route.objects.all(),
        required=False,
        allow_null=True,
    )
    applicable_operator_name = serializers.CharField(
        source="applicable_operator.name", read_only=True, default=""
    )
    applicable_route_name = serializers.CharField(source="applicable_route.name", read_only=True, default="")
    times_used = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = PromoCode
        fields = [
            "public_id",
            "code",
            "description_km",
            "description_en",
            "discount_type",
            "value",
            "min_amount",
            "max_uses",
            "max_uses_per_customer",
            "valid_from",
            "valid_until",
            "applicable_operator_id",
            "applicable_operator_name",
            "applicable_route_id",
            "applicable_route_name",
            "is_active",
            "times_used",
            "created_at",
        ]
        read_only_fields = ["public_id", "times_used", "created_at"]

    def validate_code(self, code):
        return code.strip().upper()

    def validate(self, attrs):
        valid_from = attrs.get("valid_from", getattr(self.instance, "valid_from", None))
        valid_until = attrs.get("valid_until", getattr(self.instance, "valid_until", None))
        if valid_from and valid_until and valid_until <= valid_from:
            raise serializers.ValidationError({"valid_until": "Must be after valid_from."})
        discount_type = attrs.get("discount_type", getattr(self.instance, "discount_type", None))
        value = attrs.get("value", getattr(self.instance, "value", None))
        if discount_type == PromoCode.DiscountType.PERCENT and value is not None and not 0 < value <= 100:
            raise serializers.ValidationError({"value": "A percentage must be between 0 and 100."})
        return attrs
