from rest_framework import serializers

from .models import City, Route, RouteStop, Stop


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ["public_id", "name_en", "name_km", "country_code"]


class StopSerializer(serializers.ModelSerializer):
    city = CitySerializer(read_only=True)
    city_id = serializers.SlugRelatedField(
        source="city", slug_field="public_id", queryset=City.objects.filter(is_active=True), write_only=True
    )

    class Meta:
        model = Stop
        fields = ["public_id", "city", "city_id", "name_en", "name_km", "address", "lat", "lng"]
        read_only_fields = ["public_id"]


class RouteStopSerializer(serializers.ModelSerializer):
    stop = StopSerializer(read_only=True)
    stop_id = serializers.SlugRelatedField(
        source="stop", slug_field="public_id", queryset=Stop.objects.all(), write_only=True
    )

    class Meta:
        model = RouteStop
        fields = ["public_id", "stop", "stop_id", "sequence", "offset_minutes"]
        read_only_fields = ["public_id"]


class RouteSerializer(serializers.ModelSerializer):
    origin_city = CitySerializer(read_only=True)
    destination_city = CitySerializer(read_only=True)
    origin_city_id = serializers.SlugRelatedField(
        source="origin_city", slug_field="public_id", queryset=City.objects.all(), write_only=True
    )
    destination_city_id = serializers.SlugRelatedField(
        source="destination_city", slug_field="public_id", queryset=City.objects.all(), write_only=True
    )
    stops_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Route
        fields = [
            "public_id",
            "name",
            "origin_city",
            "destination_city",
            "origin_city_id",
            "destination_city_id",
            "distance_km",
            "estimated_duration_minutes",
            "is_active",
            "stops_count",
            "created_at",
        ]
        read_only_fields = ["public_id", "stops_count", "created_at"]

    def validate(self, attrs):
        origin = attrs.get("origin_city", getattr(self.instance, "origin_city", None))
        destination = attrs.get("destination_city", getattr(self.instance, "destination_city", None))
        if origin is not None and origin == destination:
            raise serializers.ValidationError({"destination_city_id": "Must differ from the origin city."})
        return attrs


class RouteStopItemSerializer(serializers.Serializer):
    route_stop_id = serializers.SlugRelatedField(
        source="route_stop",
        slug_field="public_id",
        queryset=RouteStop.objects.all(),
        required=False,
        allow_null=True,
        help_text="Existing RouteStop to keep (omit for a newly added stop).",
    )
    stop_id = serializers.SlugRelatedField(source="stop", slug_field="public_id", queryset=Stop.objects.all())
    offset_minutes = serializers.IntegerField(min_value=0)


class RouteStopListSerializer(serializers.Serializer):
    stops = RouteStopItemSerializer(many=True, allow_empty=False, max_length=50)
