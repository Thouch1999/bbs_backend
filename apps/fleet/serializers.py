from rest_framework import serializers

from .models import Bus, Seat, SeatLayout
from .services import ALLOWED_AMENITIES, MAX_COLUMNS, MAX_ROWS, layout_in_use


class SeatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Seat
        fields = [
            "public_id",
            "seat_number",
            "deck",
            "row_position",
            "col_position",
            "seat_type",
            "is_female_only",
        ]
        read_only_fields = ["public_id"]


class SeatLayoutSerializer(serializers.ModelSerializer):
    seats = SeatSerializer(many=True, read_only=True)
    in_use = serializers.SerializerMethodField(
        help_text="True once trips were generated from this layout — its seats are then read-only."
    )
    bus_count = serializers.SerializerMethodField()

    class Meta:
        model = SeatLayout
        fields = ["public_id", "name", "bus_type", "deck_count", "seats", "in_use", "bus_count", "created_at"]
        read_only_fields = ["public_id", "deck_count", "seats", "in_use", "bus_count", "created_at"]

    def get_in_use(self, layout) -> bool:
        return layout_in_use(layout)

    def get_bus_count(self, layout) -> int:
        return layout.buses.count()


class SeatLayoutBuildSerializer(serializers.Serializer):
    decks = serializers.IntegerField(min_value=1, max_value=2, default=1)
    rows = serializers.IntegerField(min_value=1, max_value=MAX_ROWS)
    columns = serializers.ListField(
        child=serializers.CharField(max_length=1, allow_null=True, required=False),
        allow_empty=False,
        max_length=MAX_COLUMNS,
        help_text='Left-to-right row slots, e.g. ["A","B",null,"C"] (null = aisle).',
    )


class SeatInputSerializer(serializers.Serializer):
    seat_number = serializers.CharField(max_length=10)
    deck = serializers.IntegerField(min_value=1, max_value=2, default=1)
    row_position = serializers.IntegerField(min_value=1, max_value=MAX_ROWS)
    col_position = serializers.IntegerField(min_value=1, max_value=MAX_COLUMNS)
    seat_type = serializers.ChoiceField(choices=Seat.SeatType.choices, default=Seat.SeatType.NORMAL)
    is_female_only = serializers.BooleanField(default=False)


class SeatLayoutSeatsSerializer(serializers.Serializer):
    deck_count = serializers.IntegerField(min_value=1, max_value=2, default=1)
    seats = SeatInputSerializer(many=True, allow_empty=False, max_length=MAX_ROWS * MAX_COLUMNS * 2)


class BusSerializer(serializers.ModelSerializer):
    seat_layout = SeatLayoutSerializer(read_only=True)
    seat_layout_id = serializers.SlugRelatedField(
        source="seat_layout", slug_field="public_id", queryset=SeatLayout.objects.all(), write_only=True
    )
    amenities = serializers.ListField(
        child=serializers.ChoiceField(choices=ALLOWED_AMENITIES), required=False, default=list
    )

    class Meta:
        model = Bus
        fields = [
            "public_id",
            "plate_number",
            "seat_layout",
            "seat_layout_id",
            "amenities",
            "photo",
            "status",
            "registration_expires_on",
            "insurance_expires_on",
        ]
        read_only_fields = ["public_id"]

    def validate_seat_layout_id(self, seat_layout):
        operator = self.context["request"].user.operator_staff.operator
        if seat_layout.operator_id != operator.id:
            raise serializers.ValidationError("This seat layout belongs to a different operator.")
        return seat_layout

    def validate_plate_number(self, plate_number):
        plate_number = plate_number.strip().upper()
        operator = self.context["request"].user.operator_staff.operator
        clash = Bus.objects.filter(operator=operator, plate_number=plate_number)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            # Otherwise the DB's unique constraint surfaces as a 500.
            raise serializers.ValidationError("You already have a bus with this plate number.")
        return plate_number
