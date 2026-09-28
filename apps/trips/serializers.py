from decimal import Decimal

from rest_framework import serializers

from apps.accounts.models import OperatorStaff
from apps.fleet.models import Bus
from apps.fleet.serializers import SeatSerializer
from apps.routes.models import Route
from apps.routes.serializers import RouteSerializer

from .models import Trip, TripSeat


class TripSerializer(serializers.ModelSerializer):
    route = RouteSerializer(read_only=True)
    operator_name = serializers.CharField(source="route.operator.name", read_only=True)
    bus_plate_number = serializers.CharField(source="bus.plate_number", read_only=True)
    bus_type = serializers.CharField(source="bus.seat_layout.bus_type", read_only=True)
    amenities = serializers.JSONField(source="bus.amenities", read_only=True)

    class Meta:
        model = Trip
        fields = [
            "public_id",
            "route",
            "operator_name",
            "bus_plate_number",
            "bus_type",
            "amenities",
            "departure_at",
            "arrival_at",
            "base_fare_usd",
            "base_fare_khr",
            "seats_available",
            "status",
            "delay_minutes",
            "cancellation_reason",
        ]
        read_only_fields = ["public_id", "seats_available", "status", "delay_minutes", "cancellation_reason"]


class OperatorTripSerializer(TripSerializer):
    """Back-office trip row: the public fields plus seat/revenue stats
    (annotated in SQL by services.with_operator_stats — never counted per
    row in Python) and crew assignment."""

    bus_public_id = serializers.UUIDField(source="bus.public_id", read_only=True)
    # The conductor screen's "call the office" button on an invalid ticket.
    operator_phone = serializers.CharField(source="route.operator.contact_phone", read_only=True)
    seats_total = serializers.IntegerField(read_only=True)
    seats_sold = serializers.IntegerField(read_only=True)
    bookings_count = serializers.IntegerField(read_only=True)
    revenue_usd = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    revenue_khr = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    conductor_id = serializers.UUIDField(source="conductor.public_id", read_only=True, allow_null=True)
    conductor_name = serializers.CharField(source="conductor.user.full_name", read_only=True, allow_null=True)

    class Meta(TripSerializer.Meta):
        fields = TripSerializer.Meta.fields + [
            "bus_public_id",
            "operator_phone",
            "seats_total",
            "seats_sold",
            "bookings_count",
            "revenue_usd",
            "revenue_khr",
            "driver_name",
            "driver_phone",
            "conductor_id",
            "conductor_name",
        ]
        read_only_fields = fields


class _OperatorOwnedFieldsMixin:
    """Route/bus/conductor must all belong to the caller's operator."""

    def _operator(self):
        return self.context["request"].user.operator_staff.operator

    def validate_route_id(self, route):
        if route.operator_id != self._operator().id:
            raise serializers.ValidationError("This route belongs to a different operator.")
        return route

    def validate_bus_id(self, bus):
        if bus.operator_id != self._operator().id:
            raise serializers.ValidationError("This bus belongs to a different operator.")
        return bus

    def validate_conductor_id(self, conductor):
        if conductor is None:
            return None
        if conductor.operator_id != self._operator().id:
            raise serializers.ValidationError("This staff member belongs to a different operator.")
        if not conductor.can_validate_tickets:
            raise serializers.ValidationError("This staff member isn't allowed to validate tickets.")
        return conductor


_FARE_USD = {"max_digits": 8, "decimal_places": 2, "min_value": Decimal("0.01")}
_FARE_KHR = {"max_digits": 12, "decimal_places": 0, "min_value": Decimal(1)}


def _route_field():
    return serializers.SlugRelatedField(source="route", slug_field="public_id", queryset=Route.objects.all())


def _bus_field():
    return serializers.SlugRelatedField(source="bus", slug_field="public_id", queryset=Bus.objects.all())


def _conductor_field():
    return serializers.SlugRelatedField(
        source="conductor",
        slug_field="public_id",
        queryset=OperatorStaff.objects.select_related("user"),
        required=False,
        allow_null=True,
    )


class TripCreateSerializer(_OperatorOwnedFieldsMixin, serializers.Serializer):
    """Validates input only — creation/update go through services.create_trip
    / update_trip (schedule + bus-conflict checks, seat regeneration)."""

    route_id = _route_field()
    bus_id = _bus_field()
    departure_at = serializers.DateTimeField()
    arrival_at = serializers.DateTimeField()
    base_fare_usd = serializers.DecimalField(**_FARE_USD)
    base_fare_khr = serializers.DecimalField(**_FARE_KHR)
    driver_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    driver_phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    conductor_id = _conductor_field()


class RecurringTripCreateSerializer(_OperatorOwnedFieldsMixin, serializers.Serializer):
    route_id = _route_field()
    bus_id = _bus_field()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        required=False,
        default=list,
        help_text="Monday=0 .. Sunday=6. Empty = every day.",
    )
    departure_time = serializers.TimeField(help_text="Local (Asia/Phnom_Penh) time, HH:MM.")
    duration_minutes = serializers.IntegerField(
        min_value=1, required=False, help_text="Defaults to the route's estimated duration."
    )
    base_fare_usd = serializers.DecimalField(**_FARE_USD)
    base_fare_khr = serializers.DecimalField(**_FARE_KHR)
    peak_start_date = serializers.DateField(required=False, allow_null=True)
    peak_end_date = serializers.DateField(required=False, allow_null=True)
    peak_multiplier = serializers.DecimalField(
        max_digits=4, decimal_places=2, min_value=Decimal("1.00"), max_value=Decimal("5.00"),
        required=False, allow_null=True,
    )
    driver_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    driver_phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    conductor_id = _conductor_field()

    def validate(self, attrs):
        if not attrs.get("duration_minutes"):
            duration = attrs["route"].estimated_duration_minutes
            if not duration:
                raise serializers.ValidationError(
                    {"duration_minutes": "Required — this route has no estimated duration."}
                )
            attrs["duration_minutes"] = duration
        peak = [attrs.get("peak_start_date"), attrs.get("peak_end_date"), attrs.get("peak_multiplier")]
        if any(peak) and not all(peak):
            raise serializers.ValidationError(
                {"peak_multiplier": "Peak pricing needs a start date, end date and multiplier."}
            )
        if all(peak) and attrs["peak_start_date"] > attrs["peak_end_date"]:
            raise serializers.ValidationError({"peak_start_date": "Must not be after peak_end_date."})
        return attrs


class OperatorTripQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False, help_text="Local date, inclusive.")
    date_to = serializers.DateField(required=False, help_text="Local date, inclusive.")
    route = serializers.UUIDField(required=False)
    bus = serializers.UUIDField(required=False)
    status = serializers.CharField(required=False, help_text="Comma-separated statuses.")
    unassigned = serializers.BooleanField(
        required=False, default=False, help_text="Only trips with no driver or no conductor."
    )
    mine = serializers.BooleanField(
        required=False, default=False, help_text="Only trips the calling conductor is assigned to."
    )


class TripStatusPreviewSerializer(serializers.Serializer):
    bookings_count = serializers.IntegerField()
    passengers_count = serializers.IntegerField()
    channels = serializers.DictField(child=serializers.IntegerField())
    messages = serializers.DictField(child=serializers.CharField())


class TripSeatSerializer(serializers.ModelSerializer):
    seat = SeatSerializer(read_only=True)

    class Meta:
        model = TripSeat
        fields = ["public_id", "seat", "status"]
        read_only_fields = fields


class HoldSeatsSerializer(serializers.Serializer):
    seat_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, min_length=1)


class ReleaseSeatsSerializer(serializers.Serializer):
    seat_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, min_length=1)


class ReleaseResponseSerializer(serializers.Serializer):
    released = serializers.IntegerField()


class HoldResponseSerializer(serializers.Serializer):
    hold_token = serializers.CharField()
    held_until = serializers.DateTimeField()
    seat_ids = serializers.ListField(child=serializers.UUIDField())


class TripStatusUpdateSerializer(serializers.Serializer):
    # "scheduled" = back on time (only valid from delayed — see
    # services.check_status_transition).
    status = serializers.ChoiceField(
        choices=[Trip.Status.SCHEDULED, Trip.Status.DELAYED, Trip.Status.CANCELLED]
    )
    delay_minutes = serializers.IntegerField(required=False, min_value=1)
    cancellation_reason = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if attrs["status"] == Trip.Status.DELAYED and not attrs.get("delay_minutes"):
            raise serializers.ValidationError({"delay_minutes": "Required when status is delayed."})
        return attrs
