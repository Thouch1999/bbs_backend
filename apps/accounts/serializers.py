from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import OperatorStaff, SavedPassenger, User
from .services import InvalidPhoneNumberError, normalize_phone


class PhoneField(serializers.CharField):
    """Contact-phone validator (bookings/operators/support) — not used for login."""

    def to_internal_value(self, data):
        raw = super().to_internal_value(data)
        try:
            return normalize_phone(raw)
        except InvalidPhoneNumberError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["email", "password", "full_name"]

    def validate_email(self, email):
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return email

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(password=password, **validated_data)
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])


class BBMSTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["role"] = user.role
        token["preferred_language"] = user.preferred_language
        operator_staff = getattr(user, "operator_staff", None)
        token["operator_id"] = str(operator_staff.operator.public_id) if operator_staff else None
        return token


class SavedPassengerSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedPassenger
        fields = ["public_id", "full_name", "age", "gender", "phone", "id_document_number"]
        read_only_fields = ["public_id"]


class ProfileOperatorStaffSerializer(serializers.ModelSerializer):
    operator_public_id = serializers.UUIDField(source="operator.public_id", read_only=True)
    operator_name = serializers.CharField(source="operator.name", read_only=True)
    operator_name_km = serializers.CharField(source="operator.name_km", read_only=True)
    operator_status = serializers.CharField(source="operator.status", read_only=True)

    class Meta:
        model = OperatorStaff
        fields = [
            "operator_public_id",
            "operator_name",
            "operator_name_km",
            "operator_status",
            "staff_role",
            "can_create_bookings",
            "can_validate_tickets",
        ]
        read_only_fields = fields


class ProfileSerializer(serializers.ModelSerializer):
    # The JWT only carries the top-level role; the back office needs the
    # finer staff role/permission toggles (read fresh from the DB here, so a
    # permission change applies without waiting for a token refresh).
    operator_staff = ProfileOperatorStaffSerializer(read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            "public_id",
            "email",
            "phone",
            "full_name",
            "role",
            "preferred_language",
            "preferred_currency",
            "operator_staff",
        ]
        read_only_fields = ["public_id", "email", "role", "operator_staff"]



# --- Admin (Step 16) ---------------------------------------------------------
class AdminUserSerializer(serializers.ModelSerializer):
    bookings_count = serializers.IntegerField(read_only=True, default=0)
    operator_name = serializers.CharField(source="operator_staff.operator.name", read_only=True, default="")
    staff_role = serializers.CharField(source="operator_staff.staff_role", read_only=True, default="")

    class Meta:
        model = User
        fields = [
            "public_id",
            "email",
            "phone",
            "full_name",
            "role",
            "is_active",
            "preferred_language",
            "last_login",
            "created_at",
            "bookings_count",
            "operator_name",
            "staff_role",
        ]
        read_only_fields = fields


class AdminUserUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    role = serializers.ChoiceField(choices=["passenger", "admin"], required=False)


class AdminUserQuerySerializer(serializers.Serializer):
    search = serializers.CharField(required=False, help_text="Name or email.")
    role = serializers.ChoiceField(choices=User.Role.choices, required=False)
    is_active = serializers.BooleanField(required=False, allow_null=True, default=None)
