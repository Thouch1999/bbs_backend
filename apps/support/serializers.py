from rest_framework import serializers

from apps.accounts.serializers import PhoneField
from apps.bookings.models import Booking

from .models import SupportMessage, SupportTicket


class SupportMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.full_name", read_only=True, default="")

    class Meta:
        model = SupportMessage
        fields = ["public_id", "sender", "channel", "body", "author_name", "created_at"]
        read_only_fields = fields


class SupportTicketSerializer(serializers.ModelSerializer):
    pnr = serializers.CharField(source="booking.pnr", read_only=True, default="")
    booking_public_id = serializers.UUIDField(source="booking.public_id", read_only=True, default=None)
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "public_id",
            "category",
            "priority",
            "status",
            "subject",
            "contact_phone",
            "contact_name",
            "language",
            "pnr",
            "booking_public_id",
            "sla_due_at",
            "last_message_at",
            "unread_by_staff",
            "resolved_at",
            "created_at",
            "last_message_preview",
        ]
        read_only_fields = fields

    def get_last_message_preview(self, ticket) -> str:
        messages = list(ticket.messages.all())
        return messages[-1].body[:140] if messages else ""


class SupportTicketDetailSerializer(SupportTicketSerializer):
    messages = SupportMessageSerializer(many=True, read_only=True)

    class Meta(SupportTicketSerializer.Meta):
        fields = SupportTicketSerializer.Meta.fields + ["messages"]
        read_only_fields = fields


class OpenTicketSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=SupportTicket.Category.choices)
    subject = serializers.CharField(max_length=200)
    body = serializers.CharField(max_length=4000)
    booking_id = serializers.SlugRelatedField(
        slug_field="public_id", queryset=Booking.objects.all(), required=False, allow_null=True
    )
    contact_phone = PhoneField(required=False)
    contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4000)


class StaffReplySerializer(MessageCreateSerializer):
    resolve = serializers.BooleanField(default=False)


class TicketUpdateSerializer(serializers.Serializer):
    priority = serializers.ChoiceField(choices=SupportTicket.Priority.choices, required=False)
    status = serializers.ChoiceField(choices=SupportTicket.Status.choices, required=False)
    mark_read = serializers.BooleanField(required=False, default=False)


class AdminTicketQuerySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=SupportTicket.Category.choices, required=False)
    status = serializers.ChoiceField(choices=SupportTicket.Status.choices, required=False)
    search = serializers.CharField(required=False, help_text="PNR, phone, name or subject.")
