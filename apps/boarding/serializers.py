from rest_framework import serializers

from .models import BoardingRecord


class ValidateQrSerializer(serializers.Serializer):
    qr_token = serializers.CharField()


class BoardingRecordSerializer(serializers.ModelSerializer):
    passenger_name = serializers.CharField(source="passenger.full_name", read_only=True)
    seat_number = serializers.CharField(source="passenger.trip_seat.seat.seat_number", read_only=True)

    class Meta:
        model = BoardingRecord
        fields = ["public_id", "passenger_name", "seat_number", "status", "scanned_at"]
        read_only_fields = fields


class ValidateQrResponseSerializer(serializers.Serializer):
    pnr = serializers.CharField()
    boarded = BoardingRecordSerializer(many=True)


class ManifestPassengerSerializer(serializers.Serializer):
    booking_public_id = serializers.UUIDField()
    pnr = serializers.CharField()
    passenger_public_id = serializers.UUIDField()
    full_name = serializers.CharField()
    seat_number = serializers.CharField()
    boarded = serializers.BooleanField()


class OperatorManifestRowSerializer(ManifestPassengerSerializer):
    age = serializers.IntegerField(allow_null=True)
    gender = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    seat_public_id = serializers.UUIDField()
    boarding_stop_name_en = serializers.CharField(allow_blank=True)
    boarding_stop_name_km = serializers.CharField(allow_blank=True)
    booking_status = serializers.CharField()
    booking_channel = serializers.CharField()
    payment_status = serializers.ChoiceField(choices=["paid", "pay_at_counter", "pending"])
    boarding_status = serializers.CharField(allow_blank=True, help_text='"boarded", "no_show" or "".')


class MarkBoardingSerializer(serializers.Serializer):
    passenger_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=200)
    status = serializers.ChoiceField(
        choices=BoardingRecord.Status.choices, default=BoardingRecord.Status.BOARDED
    )


class MarkBoardingResponseSerializer(serializers.Serializer):
    updated = serializers.IntegerField()


class ConductorManifestRowSerializer(ManifestPassengerSerializer):
    """What the conductor app caches in IndexedDB for offline validation.
    qr_token_sha256 lets it verify a scanned QR offline without the signing
    secret (see services.qr_token_fingerprint)."""

    phone = serializers.CharField(allow_blank=True)
    boarding_stop_name_en = serializers.CharField(allow_blank=True)
    boarding_stop_name_km = serializers.CharField(allow_blank=True)
    boarding_status = serializers.CharField(allow_blank=True)
    scanned_at = serializers.DateTimeField(allow_null=True)
    qr_token_sha256 = serializers.CharField(allow_blank=True)


class BoardByPnrSerializer(serializers.Serializer):
    pnr = serializers.CharField(max_length=8, min_length=4)


class OfflineScanSerializer(serializers.Serializer):
    client_scan_id = serializers.UUIDField()
    passenger_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=BoardingRecord.Status.choices)
    scanned_at = serializers.DateTimeField(required=False, allow_null=True)


class OfflineSyncSerializer(serializers.Serializer):
    scans = OfflineScanSerializer(many=True, allow_empty=False, max_length=500)


class OfflineSyncResultSerializer(serializers.Serializer):
    client_scan_id = serializers.UUIDField()
    passenger_id = serializers.UUIDField()
    result = serializers.ChoiceField(choices=["applied", "duplicate", "superseded", "rejected"])
    reason = serializers.CharField(required=False)
