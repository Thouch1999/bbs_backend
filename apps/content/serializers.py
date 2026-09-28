from rest_framework import serializers

from apps.core.validators import validate_banner_image

from .models import Announcement, Banner


class _ScheduleValidationMixin:
    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "Must be after the start."})
        return attrs


class BannerSerializer(_ScheduleValidationMixin, serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True, validators=[validate_banner_image])

    class Meta:
        model = Banner
        fields = [
            "public_id",
            "title_km",
            "title_en",
            "body_km",
            "body_en",
            "placement",
            "background_color",
            "image",
            "link_url",
            "sort_order",
            "starts_at",
            "ends_at",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["public_id", "created_at"]

    def validate_background_color(self, value):
        if not (len(value) == 7 and value.startswith("#")):
            raise serializers.ValidationError("Use a hex color like #0D5F6E.")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise serializers.ValidationError("Use a hex color like #0D5F6E.") from exc
        return value.upper()


class AnnouncementSerializer(_ScheduleValidationMixin, serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = [
            "public_id",
            "message_km",
            "message_en",
            "severity",
            "audience",
            "starts_at",
            "ends_at",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["public_id", "created_at"]
