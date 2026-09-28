from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(PublicIdModel, TimeStampedModel, AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        PASSENGER = "passenger", "Passenger"
        OPERATOR_STAFF = "operator_staff", "Operator staff"
        ADMIN = "admin", "Admin"

    class Language(models.TextChoices):
        KM = "km", "Khmer"
        EN = "en", "English"

    class Currency(models.TextChoices):
        KHR = "KHR", "Khmer Riel"
        USD = "USD", "US Dollar"

    email = models.EmailField(unique=True)
    phone = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        help_text="E.164, e.g. +855... (contact only, not used for login)",
    )
    full_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PASSENGER)
    preferred_language = models.CharField(max_length=2, choices=Language.choices, default=Language.KM)
    preferred_currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.KHR)
    telegram_chat_id = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        help_text="Set once the user links Telegram for delivery (Step 9).",
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.email


class OperatorStaff(PublicIdModel, TimeStampedModel):
    class StaffRole(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        COUNTER_AGENT = "counter_agent", "Counter agent"
        CONDUCTOR = "conductor", "Conductor"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="operator_staff")
    operator = models.ForeignKey("operators.Operator", on_delete=models.CASCADE, related_name="staff")
    staff_role = models.CharField(max_length=20, choices=StaffRole.choices)
    can_create_bookings = models.BooleanField(default=False)
    can_validate_tickets = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_operator_staff"
        indexes = [models.Index(fields=["operator", "user"])]

    def __str__(self):
        return f"{self.user.email} @ {self.operator_id} ({self.staff_role})"


class SavedPassenger(PublicIdModel, TimeStampedModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_passengers")
    full_name = models.CharField(max_length=150)
    age = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    phone = models.CharField(max_length=16, blank=True)
    id_document_number = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "accounts_saved_passenger"

    def __str__(self):
        return self.full_name
