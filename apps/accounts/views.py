from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.permissions import IsAdmin

from .models import SavedPassenger, User
from .serializers import (
    AdminUserQuerySerializer,
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    BBMSTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileSerializer,
    RegisterSerializer,
    SavedPassengerSerializer,
)
from .services import (
    PasswordResetError,
    UserAdminError,
    request_password_reset_code,
    update_user_as_admin,
    verify_password_reset_code,
)


def _tokens_for(user):
    refresh = BBMSTokenObtainPairSerializer.get_token(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


def _reset_error_response(exc: PasswordResetError) -> Response:
    error = {"code": exc.code, "message": exc.message}
    return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class DetailResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class TokenPairResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(_tokens_for(user), status=status.HTTP_201_CREATED)


class BBMSTokenObtainPairView(TokenObtainPairView):
    serializer_class = BBMSTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=RefreshTokenSerializer, responses={205: None})
    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"detail": "refresh is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh).blacklist()
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=PasswordResetRequestSerializer, responses=DetailResponseSerializer)
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        # Always respond success, whether or not the email has an account,
        # so this endpoint can't be used to enumerate registered addresses.
        if User.objects.filter(email__iexact=email).exists():
            request_password_reset_code(email)
        return Response({"detail": "If that account exists, a reset code was sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=PasswordResetConfirmSerializer, responses=DetailResponseSerializer)
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            verify_password_reset_code(email, serializer.validated_data["code"])
        except PasswordResetError as exc:
            return _reset_error_response(exc)

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response({"detail": "If that account exists, the password was reset."})

        if not user.is_active:
            # Matches password-login: a locked account can't be reactivated
            # by simply setting a new password.
            error = {"code": "account_locked", "message": "This account has been locked."}
            return Response({"error": error}, status=status.HTTP_403_FORBIDDEN)

        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response({"detail": "Password reset."})


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class SavedPassengerViewSet(viewsets.ModelViewSet):
    serializer_class = SavedPassengerSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def get_queryset(self):
        return SavedPassenger.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)



# --- Admin (Step 16) ---------------------------------------------------------
def _admin_users():
    return User.objects.select_related("operator_staff__operator").annotate(
        bookings_count=Count("bookings", distinct=True)
    )


@extend_schema(parameters=[AdminUserQuerySerializer])
class AdminUserListView(generics.ListAPIView):
    serializer_class = AdminUserSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        query = AdminUserQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        qs = _admin_users().order_by("-created_at")
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(Q(full_name__icontains=term) | Q(email__icontains=term))
        if params.get("role"):
            qs = qs.filter(role=params["role"])
        if params.get("is_active") is not None:
            qs = qs.filter(is_active=params["is_active"])
        return qs


class AdminUserDetailView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(responses=AdminUserSerializer)
    def get(self, request, public_id):
        user = generics.get_object_or_404(_admin_users(), public_id=public_id)
        return Response(AdminUserSerializer(user).data)

    @extend_schema(request=AdminUserUpdateSerializer, responses={200: AdminUserSerializer})
    def patch(self, request, public_id):
        user = generics.get_object_or_404(User, public_id=public_id)
        serializer = AdminUserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            update_user_as_admin(user, actor=request.user, **serializer.validated_data)
        except UserAdminError as exc:
            error = {"code": exc.code, "message": str(exc)}
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AdminUserSerializer(_admin_users().get(pk=user.pk)).data)
