from rest_framework import serializers

from apps.accounts.serializers import PhoneField
from apps.routes.models import RouteStop
from apps.trips.models import Trip

from .models import Booking, BookingPassenger


class PassengerInputSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150)
    age = serializers.IntegerField(required=False, min_value=0, max_value=120)
    gender = serializers.ChoiceField(choices=BookingPassenger.Gender.choices, required=False, default="")
    phone = serializers.CharField(required=False, allow_blank=True, max_length=16)
    id_document_number = serializers.CharField(required=False, allow_blank=True, max_length=50)


class CreateBookingSerializer(serializers.Serializer):
    trip_id = serializers.SlugRelatedField(slug_field="public_id", queryset=Trip.objects.all())
    hold_token = serializers.CharField()
    passengers = PassengerInputSerializer(many=True, allow_empty=False)
    contact_phone = PhoneField()
    contact_email = serializers.EmailField(required=False, allow_null=True)
    currency = serializers.ChoiceField(choices=Booking.Currency.choices, default=Booking.Currency.USD)
    boarding_stop_id = serializers.SlugRelatedField(
        slug_field="public_id", queryset=RouteStop.objects.all(), required=False, allow_null=True
    )
    drop_stop_id = serializers.SlugRelatedField(
        slug_field="public_id", queryset=RouteStop.objects.all(), required=False, allow_null=True
    )
    promo_code = serializers.CharField(required=False, allow_blank=True, max_length=30)


class BookingPassengerSerializer(serializers.ModelSerializer):
    seat_number = serializers.CharField(source="trip_seat.seat.seat_number", read_only=True)

    class Meta:
        model = BookingPassenger
        fields = ["public_id", "full_name", "age", "gender", "phone", "id_document_number", "seat_number"]
        read_only_fields = fields


class BookingSerializer(serializers.ModelSerializer):
    passengers = BookingPassengerSerializer(many=True, read_only=True)
    trip_public_id = serializers.CharField(source="trip.public_id", read_only=True)
    departure_at = serializers.DateTimeField(source="trip.departure_at", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "public_id",
            "pnr",
            "trip_public_id",
            "departure_at",
            "status",
            "booking_channel",
            "currency",
            "subtotal_amount",
            "discount_amount",
            "fee_amount",
            "total_amount",
            "promo_code",
            "contact_phone",
            "contact_email",
            "qr_token",
            "passengers",
            "cancelled_at",
            "cancellation_reason",
            "refund_percentage",
            "refund_amount",
            "rescheduled_at",
            "reschedule_fee_amount",
            "fare_difference_amount",
            "created_at",
        ]
        read_only_fields = fields


class CancelBookingSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255)


class RescheduleBookingSerializer(serializers.Serializer):
    new_trip_id = serializers.SlugRelatedField(slug_field="public_id", queryset=Trip.objects.all())
    new_hold_token = serializers.CharField()


class GuestLookupSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=8)
    phone = PhoneField()


class CancellationPreviewSerializer(serializers.Serializer):
    refund_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    refund_amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class ReschedulePreviewRequestSerializer(serializers.Serializer):
    new_trip_id = serializers.SlugRelatedField(slug_field="public_id", queryset=Trip.objects.all())


class ReschedulePreviewSerializer(serializers.Serializer):
    fare_difference_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    reschedule_fee_amount = serializers.DecimalField(max_digits=10, decimal_places=2)


class OperatorBookingSerializer(BookingSerializer):
    """Back-office booking row: the passenger-facing fields plus trip,
    boarding point, who sold it, and the pending pay-at-counter payment (if
    any) the counter agent can mark paid. Expects the queryset built by
    OperatorBookingListView (payments/passengers prefetched)."""

    route_name = serializers.SerializerMethodField()
    bus_plate_number = serializers.CharField(source="trip.bus.plate_number", read_only=True)
    trip_status = serializers.CharField(source="trip.status", read_only=True)
    # default="" covers a booking with no boarding/drop stop or no staff seller.
    boarding_stop_name_en = serializers.CharField(
        source="boarding_stop.stop.name_en", default="", read_only=True
    )
    boarding_stop_name_km = serializers.CharField(
        source="boarding_stop.stop.name_km", default="", read_only=True
    )
    drop_stop_name_en = serializers.CharField(source="drop_stop.stop.name_en", default="", read_only=True)
    drop_stop_name_km = serializers.CharField(source="drop_stop.stop.name_km", default="", read_only=True)
    booked_by_staff_name = serializers.CharField(
        source="booked_by_staff.full_name", default="", read_only=True
    )
    payment_provider = serializers.SerializerMethodField()
    pending_counter_payment_id = serializers.SerializerMethodField()

    class Meta(BookingSerializer.Meta):
        fields = BookingSerializer.Meta.fields + [
            "route_name",
            "bus_plate_number",
            "trip_status",
            "boarding_stop_name_en",
            "boarding_stop_name_km",
            "drop_stop_name_en",
            "drop_stop_name_km",
            "booked_by_staff_name",
            "payment_provider",
            "pending_counter_payment_id",
        ]
        read_only_fields = fields

    def get_route_name(self, booking) -> str:
        route = booking.trip.route
        return route.name or f"{route.origin_city.name_en} → {route.destination_city.name_en}"

    def _latest_payment(self, booking):
        payments = sorted(booking.payments.all(), key=lambda p: p.created_at, reverse=True)
        succeeded = [p for p in payments if p.status == "succeeded"]
        return (succeeded or payments or [None])[0]

    def get_payment_provider(self, booking) -> str:
        payment = self._latest_payment(booking)
        return payment.provider if payment else ""

    def get_pending_counter_payment_id(self, booking) -> str | None:
        for payment in booking.payments.all():
            if payment.provider == "counter" and payment.status == "pending":
                return str(payment.public_id)
        return None


class OperatorBookingQuerySerializer(serializers.Serializer):
    trip = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=Booking.Status.choices, required=False)
    channel = serializers.ChoiceField(choices=Booking.Channel.choices, required=False)
    search = serializers.CharField(required=False, help_text="PNR, contact phone or passenger name.")
    date_from = serializers.DateField(required=False, help_text="Departure local date, inclusive.")
    date_to = serializers.DateField(required=False, help_text="Departure local date, inclusive.")
    pending_counter = serializers.BooleanField(
        required=False, default=False, help_text="Only bookings awaiting a pay-at-counter payment."
    )


class CustomerLookupQuerySerializer(serializers.Serializer):
    phone = PhoneField()


class CustomerLookupPassengerSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    age = serializers.IntegerField(allow_null=True)
    gender = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class CustomerLookupSerializer(serializers.Serializer):
    contact_phone = serializers.CharField()
    contact_email = serializers.EmailField(allow_null=True)
    bookings_count = serializers.IntegerField()
    passengers = CustomerLookupPassengerSerializer(many=True)
