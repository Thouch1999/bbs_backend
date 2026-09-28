from django.db import models

from apps.core.models import PublicIdModel, TimeStampedModel


class Payment(PublicIdModel, TimeStampedModel):
    class Provider(models.TextChoices):
        ABA_PAYWAY = "aba_payway", "ABA PayWay"
        WING = "wing", "Wing"
        ACLEDA = "acleda", "ACLEDA"
        COUNTER = "counter", "Pay at counter"
        MOCK = "mock", "Mock (dev/test)"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=15, choices=Provider.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    # Null (not blank-string) until the gateway assigns one, so the DB can
    # enforce uniqueness once it's set: MySQL/MariaDB unique indexes treat
    # every NULL as distinct, so any number of not-yet-assigned rows can
    # coexist, but two rows can never share the same real provider_txn_id.
    # (A UniqueConstraint(condition=...) — the Postgres-style partial index —
    # is silently NOT enforced on MySQL/MariaDB; don't reach for that here.)
    provider_txn_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payments_payment"
        indexes = [models.Index(fields=["booking", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_txn_id"], name="unique_provider_txn_id"),
        ]

    def __str__(self):
        return f"{self.booking.pnr}:{self.provider}:{self.status}"


class Refund(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_refund_id = models.CharField(max_length=100, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payments_refund"

    def __str__(self):
        return f"{self.payment_id}:{self.amount}:{self.status}"


class WebhookLog(PublicIdModel, TimeStampedModel):
    """Every raw gateway callback, valid or not — for reconciliation (Step 10)."""

    provider = models.CharField(max_length=15)
    raw_body = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)
    signature_valid = models.BooleanField(default=False)
    payment = models.ForeignKey(
        Payment, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_logs"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "payments_webhook_log"
        indexes = [models.Index(fields=["provider", "created_at"])]


class PromoCode(PublicIdModel, TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percent"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=30, unique=True, db_index=True)
    # The passenger site shows one language at a time, so each language's
    # copy is stored separately (CLAUDE.md: never stack km and en).
    description_km = models.CharField(max_length=255, blank=True)
    description_en = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices)
    value = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Percent (0-100) or a fixed amount."
    )
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    max_uses_per_customer = models.PositiveIntegerField(null=True, blank=True)
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField()
    applicable_operator = models.ForeignKey(
        "operators.Operator", null=True, blank=True, on_delete=models.CASCADE, related_name="promo_codes"
    )
    applicable_route = models.ForeignKey(
        "routes.Route", null=True, blank=True, on_delete=models.CASCADE, related_name="promo_codes"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "payments_promo_code"

    def __str__(self):
        return self.code


class OperatorPayout(PublicIdModel, TimeStampedModel):
    """
    One settlement the platform paid out to an operator (A3 "batch payout"),
    in a single currency. The amount owed is derived (succeeded payments −
    refunds − commission − earlier payouts, see services.operator_balances);
    this row records that it was actually transferred, by whom, and the bank
    reference — so "pending payout" is always earned minus recorded payouts.
    """

    operator = models.ForeignKey("operators.Operator", on_delete=models.PROTECT, related_name="payouts")
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    period_start = models.DateField()
    period_end = models.DateField()
    reference = models.CharField(max_length=100, blank=True, help_text="Bank transfer reference.")
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField()
    paid_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="payouts_recorded"
    )

    class Meta:
        db_table = "payments_operator_payout"
        indexes = [models.Index(fields=["operator", "currency"])]
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.operator_id}:{self.amount} {self.currency}"
