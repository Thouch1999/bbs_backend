from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    """The uniform error shape every endpoint returns — see
    apps.core.exceptions.bbms_exception_handler."""

    error = ErrorDetailSerializer()


class AuditLogSerializer(serializers.Serializer):
    public_id = serializers.UUIDField()
    action = serializers.CharField()
    target_model = serializers.CharField()
    target_id = serializers.CharField()
    before = serializers.JSONField()
    after = serializers.JSONField()
    actor_name = serializers.CharField(source="actor.full_name", default="")
    actor_email = serializers.CharField(source="actor.email", default="")
    created_at = serializers.DateTimeField()


class AuditLogQuerySerializer(serializers.Serializer):
    action = serializers.CharField(required=False)
    target_model = serializers.CharField(required=False)
    target_id = serializers.CharField(required=False)
    booking = serializers.UUIDField(
        required=False,
        help_text="Everything about one booking: the booking itself and refunds on its payments.",
    )
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
