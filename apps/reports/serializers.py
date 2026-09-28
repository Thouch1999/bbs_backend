from rest_framework import serializers


class DateRangeQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        date_from, date_to = attrs.get("date_from"), attrs.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError({"date_from": "Must not be after date_to."})
        return attrs


class RevenueQuerySerializer(DateRangeQuerySerializer):
    group_by = serializers.ChoiceField(
        choices=["route", "bus", "payment_method", "channel", "day"], default="route"
    )


class RevenueRowSerializer(serializers.Serializer):
    route_public_id = serializers.UUIDField(required=False)
    route_name = serializers.CharField(required=False)
    bus_public_id = serializers.UUIDField(required=False)
    plate_number = serializers.CharField(required=False)
    provider = serializers.CharField(required=False)
    channel = serializers.CharField(required=False)
    date = serializers.DateField(required=False)
    currency = serializers.CharField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    payment_count = serializers.IntegerField()


class OccupancyRowSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    departure_at = serializers.DateTimeField()
    route_public_id = serializers.UUIDField()
    route_name = serializers.CharField(allow_blank=True)
    total_seats = serializers.IntegerField()
    booked_seats = serializers.IntegerField()
    occupancy_rate = serializers.DecimalField(max_digits=5, decimal_places=2)


class CancellationSummarySerializer(serializers.Serializer):
    cancelled_count = serializers.IntegerField()
    total_refund_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_refund_khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class _CurrencyPair(serializers.Serializer):
    usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class PlatformTotalsSerializer(serializers.Serializer):
    total_bookings = serializers.IntegerField()
    total_passengers = serializers.IntegerField()
    revenue = _CurrencyPair()
    commission = _CurrencyPair()
    active_operators = serializers.IntegerField()
    average_occupancy = serializers.DecimalField(max_digits=5, decimal_places=2)
    cancellation_rate = serializers.DecimalField(max_digits=5, decimal_places=2)


class TopRouteSerializer(serializers.Serializer):
    origin_city_name = serializers.CharField()
    origin_city_name_km = serializers.CharField()
    destination_city_name = serializers.CharField()
    destination_city_name_km = serializers.CharField()
    bookings_count = serializers.IntegerField()
    operators_count = serializers.IntegerField()
    revenue_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    revenue_khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class ReconciliationRowSerializer(serializers.Serializer):
    operator_public_id = serializers.UUIDField()
    operator_name = serializers.CharField()
    currency = serializers.CharField()
    gateway_settled = serializers.DecimalField(max_digits=14, decimal_places=2)
    platform_recorded = serializers.DecimalField(max_digits=14, decimal_places=2)
    refunded = serializers.DecimalField(max_digits=14, decimal_places=2)
    commission_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    operator_payable = serializers.DecimalField(max_digits=14, decimal_places=2)


class OperatorSummaryRowSerializer(serializers.Serializer):
    operator_public_id = serializers.UUIDField()
    operator_name = serializers.CharField()
    bookings_count = serializers.IntegerField()
    seats_sold = serializers.IntegerField()
    seats_total = serializers.IntegerField()
    occupancy_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    revenue_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    revenue_khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class DailySeriesPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    bookings = serializers.IntegerField()
    revenue_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    revenue_khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class SystemStatusRowSerializer(serializers.Serializer):
    key = serializers.CharField()
    kind = serializers.ChoiceField(choices=["payment", "notification"])
    label = serializers.CharField()
    mode = serializers.ChoiceField(choices=["live", "test", "console", "not_implemented"])
    health = serializers.ChoiceField(choices=["ok", "degraded", "down"])
    last_success_at = serializers.DateTimeField(allow_null=True)
    last_webhook_at = serializers.DateTimeField(allow_null=True)
    failures_24h = serializers.IntegerField()
    pending = serializers.IntegerField()


class PendingOperatorSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    name = serializers.CharField()
    name_km = serializers.CharField(allow_blank=True)
    licence_number = serializers.CharField(allow_blank=True)
    licence_document = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField()


class AdminOverviewSerializer(serializers.Serializer):
    totals = PlatformTotalsSerializer()
    pending_operators = PendingOperatorSerializer(many=True)
    pending_operators_count = serializers.IntegerField()
    series = DailySeriesPointSerializer(many=True)
    top_routes = TopRouteSerializer(many=True)
    system_status = SystemStatusRowSerializer(many=True)


class ManifestExportQuerySerializer(serializers.Serializer):
    # Not named "format": DRF's DefaultContentNegotiation reserves that query
    # param name (URL_FORMAT_OVERRIDE) to pick a response renderer, and a
    # value it doesn't recognize (csv/pdf aren't registered renderers) breaks
    # routing before this view ever runs.
    export_format = serializers.ChoiceField(choices=["csv", "pdf"], default="csv")


class CurrencyTotalsSerializer(serializers.Serializer):
    usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    khr = serializers.DecimalField(max_digits=14, decimal_places=2)


class DashboardTripSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    route_name = serializers.CharField(allow_blank=True)
    plate_number = serializers.CharField()
    departure_at = serializers.DateTimeField()
    status = serializers.CharField()
    delay_minutes = serializers.IntegerField(allow_null=True)


class DashboardAttentionSerializer(serializers.Serializer):
    pending_counter_payments = serializers.IntegerField()
    refunds_to_process = serializers.IntegerField()
    unassigned_trips = serializers.IntegerField()
    expiring_bus_documents = serializers.IntegerField()


class DashboardSeriesPointSerializer(serializers.Serializer):
    date = serializers.DateField()
    currency = serializers.CharField()
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class DashboardTopRouteSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    name = serializers.CharField(allow_blank=True)
    seats_sold = serializers.IntegerField()


class OperatorDashboardSerializer(serializers.Serializer):
    date = serializers.DateField()
    trips_today = serializers.IntegerField()
    trips_remaining_today = serializers.IntegerField()
    bookings_today = serializers.IntegerField()
    seats_sold_today = serializers.IntegerField()
    seats_total_today = serializers.IntegerField()
    occupancy_rate_today = serializers.DecimalField(max_digits=5, decimal_places=2)
    revenue_today = CurrencyTotalsSerializer()
    delayed_trips = DashboardTripSerializer(many=True)
    departing_soon = DashboardTripSerializer(many=True)
    attention = DashboardAttentionSerializer()
    revenue_series = DashboardSeriesPointSerializer(many=True)
    top_routes = DashboardTopRouteSerializer(many=True)
