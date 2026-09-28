from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.accounts.models import OperatorStaff
from apps.accounts.serializers import PhoneField
from apps.core.validators import validate_licence_document

from .models import Operator, OperatorReviewNote
from .services import CREATABLE_STAFF_ROLES, REVIEW_CHECK_KEYS, REVIEW_CHECK_VALUES


class OperatorSerializer(serializers.ModelSerializer):
    # What an admin asked this operator to send (OperatorReviewNote kind
    # info_request) - shown to the owner while the application is pending.
    info_requests = serializers.SerializerMethodField()
    licence_document = serializers.FileField(
        required=False, allow_null=True, validators=[validate_licence_document]
    )

    class Meta:
        model = Operator
        fields = [
            "public_id",
            "name",
            "name_km",
            "licence_number",
            "licence_document",
            "contact_phone",
            "contact_email",
            "address",
            "commission_rate",
            "status",
            "rejection_reason",
            "approved_at",
            "created_at",
            "info_requests",
        ]
        read_only_fields = [
            "public_id",
            "commission_rate",
            "status",
            "rejection_reason",
            "approved_at",
            "created_at",
            "info_requests",
        ]

    def get_info_requests(self, operator) -> list[dict]:
        notes = [n for n in operator.review_notes.all() if n.kind == "info_request"]
        return [{"body": n.body, "created_at": n.created_at} for n in notes]


class OperatorRegisterSerializer(serializers.ModelSerializer):
    contact_phone = PhoneField()
    licence_document = serializers.FileField(
        required=False, allow_null=True, validators=[validate_licence_document]
    )

    class Meta:
        model = Operator
        fields = [
            "name",
            "name_km",
            "licence_number",
            "licence_document",
            "contact_phone",
            "contact_email",
            "address",
        ]


class StaffMemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    last_login = serializers.DateTimeField(source="user.last_login", read_only=True)

    class Meta:
        model = OperatorStaff
        fields = [
            "public_id",
            "email",
            "full_name",
            "staff_role",
            "can_create_bookings",
            "can_validate_tickets",
            "is_active",
            "last_login",
            "created_at",
        ]
        read_only_fields = fields


class StaffMemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, validators=[validate_password])
    staff_role = serializers.ChoiceField(choices=CREATABLE_STAFF_ROLES)
    can_create_bookings = serializers.BooleanField(required=False, allow_null=True, default=None)
    can_validate_tickets = serializers.BooleanField(required=False, allow_null=True, default=None)


class StaffMemberUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=150, required=False)
    staff_role = serializers.ChoiceField(choices=CREATABLE_STAFF_ROLES, required=False)
    can_create_bookings = serializers.BooleanField(required=False)
    can_validate_tickets = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class OperatorApproveSerializer(serializers.Serializer):
    commission_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, min_value=0, max_value=50
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class OperatorRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)



class OperatorReviewNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.full_name", read_only=True, default="")

    class Meta:
        model = OperatorReviewNote
        fields = ["public_id", "kind", "body", "author_name", "created_at"]
        read_only_fields = ["public_id", "author_name", "created_at"]


class AdminOperatorSerializer(OperatorSerializer):
    """Admin queue/detail: the operator plus what the reviewer needs next to it."""

    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()
    staff_count = serializers.IntegerField(read_only=True, default=0)
    routes_count = serializers.IntegerField(read_only=True, default=0)
    buses_count = serializers.IntegerField(read_only=True, default=0)
    trips_count = serializers.IntegerField(read_only=True, default=0)
    review_checklist = serializers.JSONField(read_only=True)
    review_notes = OperatorReviewNoteSerializer(many=True, read_only=True)

    class Meta(OperatorSerializer.Meta):
        fields = OperatorSerializer.Meta.fields + [
            "owner_name",
            "owner_email",
            "staff_count",
            "routes_count",
            "buses_count",
            "trips_count",
            "review_checklist",
            "review_notes",
        ]
        read_only_fields = fields

    def _owner(self, operator):
        owners = [s for s in operator.staff.all() if s.staff_role == OperatorStaff.StaffRole.OWNER]
        return owners[0].user if owners else None

    def get_owner_name(self, operator) -> str:
        owner = self._owner(operator)
        return owner.full_name if owner else ""

    def get_owner_email(self, operator) -> str:
        owner = self._owner(operator)
        return owner.email if owner else ""


class ReviewChecklistSerializer(serializers.Serializer):
    checklist = serializers.DictField(child=serializers.ChoiceField(choices=REVIEW_CHECK_VALUES))

    def validate_checklist(self, checklist):
        unknown = set(checklist) - set(REVIEW_CHECK_KEYS)
        if unknown:
            raise serializers.ValidationError(f"Unknown checklist items: {sorted(unknown)}")
        return checklist


class ReviewNoteCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=2000)
    kind = serializers.ChoiceField(
        choices=OperatorReviewNote.Kind.choices, default=OperatorReviewNote.Kind.NOTE
    )


class OperatorSuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class OperatorReinstateSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
