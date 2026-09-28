"""
Payment lifecycle: initiate -> webhook (idempotent) -> confirm/cancel the
booking. Plus refunds and promo code validation. Business logic only —
views just translate HTTP <-> these calls, per CLAUDE.md's hard rules.
"""

import uuid
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.core.audit import log_action
from apps.core.models import AuditLog
from apps.notifications import services as notification_services
from apps.trips import services as trip_services

from .gateways import InvalidSignatureError, get_gateway
from .gateways.base import GatewayCallbackResult
from .models import OperatorPayout, Payment, PromoCode, Refund, WebhookLog


class PaymentError(Exception):
    code = "payment_error"


class PaymentStateError(PaymentError):
    code = "payment_state_error"


class PromoCodeError(Exception):
    code = "promo_code_error"


class PromoCodeNotFoundError(PromoCodeError):
    code = "promo_code_not_found"


class PromoCodeExpiredError(PromoCodeError):
    code = "promo_code_expired"


class PromoCodeMinAmountError(PromoCodeError):
    code = "promo_code_min_amount_not_met"


class PromoCodeNotApplicableError(PromoCodeError):
    code = "promo_code_not_applicable"


class PromoCodeUsageLimitError(PromoCodeError):
    code = "promo_code_usage_limit_reached"


def _quantize(amount: Decimal) -> Decimal:
    return Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def initiate_payment(booking: Booking, provider: str) -> tuple[Payment, dict]:
    if booking.status != Booking.Status.PENDING_PAYMENT:
        raise PaymentStateError(
            _("Booking %(pnr)s is %(status)s, not pending_payment.")
            % {"pnr": booking.pnr, "status": booking.status}
        )

    payment = Payment.objects.create(
        booking=booking, provider=provider, amount=booking.total_amount, currency=booking.currency
    )

    if provider == Payment.Provider.COUNTER:
        seat_ids = list(booking.passengers.values_list("trip_seat__seat_id", flat=True))
        deadline = timezone.now() + timedelta(minutes=settings.COUNTER_PAYMENT_DEADLINE_MINUTES)
        trip_services.extend_hold(booking.trip_id, seat_ids, deadline)
        return payment, {"pay_at_counter": True, "deadline": deadline.isoformat()}

    gateway = get_gateway(provider)
    gateway_payload = gateway.initiate(payment)

    if provider == Payment.Provider.DEMO:
        # No real gateway exists to send a webhook back, so this
        # presentation-only provider confirms itself immediately instead of
        # leaving the passenger stuck waiting on a callback nobody will ever
        # send. Goes through the exact same atomic/idempotent path a real
        # webhook would (booking confirm + notification fan-out included).
        # Deliberately separate from Provider.MOCK, whose whole purpose is
        # staying pending until *tests* send it a signed webhook — reusing
        # MOCK here would silently break that.
        payment = _apply_webhook_result(
            payment.pk,
            provider=provider,
            result=GatewayCallbackResult(
                merchant_ref=str(payment.public_id),
                provider_txn_id=f"demo-{uuid.uuid4().hex[:12]}",
                succeeded=True,
                raw={"demo_auto_confirm": True},
            ),
        )

    return payment, gateway_payload


def mark_counter_payment_paid(payment: Payment) -> Payment:
    """Staff confirms a pay-at-counter payment was received in cash (FR4.3)."""
    if payment.provider != Payment.Provider.COUNTER:
        raise PaymentStateError(_("Only pay-at-counter payments can be marked paid this way."))
    if payment.status != Payment.Status.PENDING:
        raise PaymentStateError(_("Payment is already %(status)s.") % {"status": payment.status})

    payment.status = Payment.Status.SUCCEEDED
    payment.save(update_fields=["status", "updated_at"])
    booking_services.confirm_booking(payment.booking)

    notification_services.notify_payment_receipt(payment)
    return payment


def process_webhook(provider: str, request) -> Payment | None:
    """
    Idempotent: replaying the same callback (same merchant_ref, already
    processed) is a no-op — confirm_booking/cancel_booking each only run
    once, the first time this payment leaves PENDING. Every raw callback is
    logged via WebhookLog regardless of outcome, for reconciliation (Step
    10) — each log write commits on its own, outside any atomic block, so a
    later failure while processing a *valid* callback (e.g. confirm_booking
    raising) can never roll back its own log entry along with it. Only
    caught once, against a real (InnoDB) MySQL server: this project's dev
    database had silently been running on MyISAM (WAMP's own
    default_storage_engine, not a Django/BBMS default), whose non-
    transactional tables happened to keep the log row even when the whole
    function really was one big @transaction.atomic that rolled it back —
    see config/settings/base.py's init_command and git history for the fix.
    """
    gateway = get_gateway(provider)

    try:
        result = gateway.verify_callback(request)
    except InvalidSignatureError as exc:
        WebhookLog.objects.create(
            provider=provider,
            raw_body=request.body.decode(errors="replace"),
            headers=dict(request.headers),
            signature_valid=False,
            note=str(exc),
        )
        raise

    payment = Payment.objects.filter(public_id=result.merchant_ref).first()
    WebhookLog.objects.create(
        provider=provider,
        raw_body=request.body.decode(errors="replace"),
        headers=dict(request.headers),
        signature_valid=True,
        payment=payment,
        note="" if payment else "no matching payment for merchant_ref",
    )
    if payment is None:
        return None

    return _apply_webhook_result(payment.pk, provider=provider, result=result)


@transaction.atomic
def _apply_webhook_result(payment_id, *, provider: str, result) -> Payment:
    """The actual payment/booking state change — this part alone needs
    atomicity (the select_for_update + status flip + booking confirm/cancel
    must all happen together), unlike the logging above it."""
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status != Payment.Status.PENDING:
        return payment  # replay of an already-processed callback — no-op

    payment.provider_txn_id = result.provider_txn_id
    payment.raw_payload = result.raw
    payment.status = Payment.Status.SUCCEEDED if result.succeeded else Payment.Status.FAILED
    payment.save(update_fields=["provider_txn_id", "raw_payload", "status", "updated_at"])

    if result.succeeded:
        booking_services.confirm_booking(payment.booking)
        notification_services.notify_payment_receipt(payment)
    else:
        booking_services.cancel_booking(payment.booking, reason=f"Payment failed via {provider} webhook.")

    return payment


def refundable_balance(payment: Payment) -> Decimal:
    """What's still refundable: the amount paid minus every succeeded refund."""
    refunded = sum(
        (r.amount for r in payment.refunds.filter(status=Refund.Status.SUCCEEDED)), Decimal("0.00")
    )
    return _quantize(payment.amount - refunded)


def create_refund(payment: Payment, amount: Decimal, *, reason: str = "", actor=None) -> Refund:
    if payment.status != Payment.Status.SUCCEEDED:
        raise PaymentStateError(_("Only a succeeded payment can be refunded."))
    amount = _quantize(amount)
    # Cap across *all* refunds on this payment, not just this one — two
    # partial refunds could otherwise add up to more than was ever paid.
    remaining = refundable_balance(payment)
    if amount <= 0 or amount > remaining:
        raise PaymentStateError(
            _("Refund amount must be between 0 and %(remaining)s.") % {"remaining": remaining}
        )

    gateway = get_gateway(payment.provider)
    result = gateway.refund(payment, amount)  # raises GatewayNotImplementedError for real, unwired gateways

    refund = Refund.objects.create(
        payment=payment,
        amount=amount,
        reason=reason,
        status=Refund.Status.SUCCEEDED if result.success else Refund.Status.FAILED,
        provider_refund_id=result.provider_refund_id,
        raw_payload=result.raw,
    )
    if refund.status == Refund.Status.SUCCEEDED:
        notification_services.notify_refund_processed(refund)
    log_action(
        actor=actor,
        action=AuditLog.Action.REFUND_ISSUED,
        target=refund,
        before={},
        after={"amount": str(refund.amount), "status": refund.status, "reason": reason},
    )
    return refund


def validate_promo_code(
    code: str, *, subtotal: Decimal, trip=None, user=None, contact_phone: str = ""
) -> Decimal:
    """Returns the discount amount (quantized, never more than subtotal) or raises PromoCodeError."""
    try:
        promo = PromoCode.objects.get(code=code, is_active=True)
    except PromoCode.DoesNotExist as exc:
        raise PromoCodeNotFoundError(
            _("Promo code '%(code)s' does not exist or is inactive.") % {"code": code}
        ) from exc

    now = timezone.now()
    if not (promo.valid_from <= now <= promo.valid_until):
        raise PromoCodeExpiredError(_("Promo code '%(code)s' is not currently valid.") % {"code": code})

    if promo.min_amount is not None and subtotal < promo.min_amount:
        raise PromoCodeMinAmountError(
            _("Promo code '%(code)s' requires a minimum of %(min_amount)s.")
            % {"code": code, "min_amount": promo.min_amount}
        )

    if trip is not None:
        if promo.applicable_operator_id and trip.route.operator_id != promo.applicable_operator_id:
            raise PromoCodeNotApplicableError(
                _("Promo code '%(code)s' does not apply to this operator.") % {"code": code}
            )
        if promo.applicable_route_id and trip.route_id != promo.applicable_route_id:
            raise PromoCodeNotApplicableError(
                _("Promo code '%(code)s' does not apply to this route.") % {"code": code}
            )

    active_usages = Booking.objects.filter(promo_code=code).exclude(status=Booking.Status.CANCELLED)
    if promo.max_uses is not None and active_usages.count() >= promo.max_uses:
        raise PromoCodeUsageLimitError(
            _("Promo code '%(code)s' has reached its usage limit.") % {"code": code}
        )

    if promo.max_uses_per_customer is not None:
        if user is not None:
            per_customer = active_usages.filter(user=user).count()
        elif contact_phone:
            per_customer = active_usages.filter(contact_phone=contact_phone).count()
        else:
            per_customer = 0
        if per_customer >= promo.max_uses_per_customer:
            raise PromoCodeUsageLimitError(
                f"Promo code '{code}' has already been used the maximum number of times."
            )

    if promo.discount_type == PromoCode.DiscountType.PERCENT:
        discount = subtotal * promo.value / 100
    else:
        discount = promo.value

    return _quantize(min(discount, subtotal))


# --- Admin reconciliation (Step 16, A3) --------------------------------------
#
# A "mismatch" is a succeeded payment the platform's own records disagree
# with: the booking never got confirmed, it was charged in a different
# currency than the booking, or the amount differs from the booking total
# (rescheduled bookings are excluded - their total legitimately changes
# after payment). Defined once as SQL for counts/filters, mirrored in
# payment_flags() for the per-row "investigate" view.
MISMATCH_FLAGS = ("paid_not_confirmed", "currency_mismatch", "amount_mismatch")
STALE_PENDING_HOURS = 24


def mismatch_q() -> Q:
    return Q(status=Payment.Status.SUCCEEDED) & (
        Q(booking__status=Booking.Status.PENDING_PAYMENT)
        | ~Q(currency=F("booking__currency"))
        | (Q(booking__rescheduled_at__isnull=True) & ~Q(amount=F("booking__total_amount")))
    )


def _succeeded_refunds_total(payment: Payment) -> Decimal:
    return sum(
        (r.amount for r in payment.refunds.all() if r.status == Refund.Status.SUCCEEDED), Decimal("0.00")
    )


def payment_flags(payment: Payment, *, now=None) -> list[str]:
    """Every reconciliation flag for one payment (expects refunds prefetched)."""
    now = now or timezone.now()
    booking = payment.booking
    flags = []
    if payment.status == Payment.Status.SUCCEEDED:
        if booking.status == Booking.Status.PENDING_PAYMENT:
            flags.append("paid_not_confirmed")
        if payment.currency != booking.currency:
            flags.append("currency_mismatch")
        elif booking.rescheduled_at is None and payment.amount != booking.total_amount:
            flags.append("amount_mismatch")
        refund_owed = booking.refund_amount or Decimal("0.00")
        if booking.status == Booking.Status.CANCELLED and refund_owed > _succeeded_refunds_total(payment):
            flags.append("refund_due")
    elif payment.status == Payment.Status.PENDING:
        if payment.created_at < now - timedelta(hours=STALE_PENDING_HOURS):
            flags.append("stale_pending")
    return flags


def reconciliation_status(payment: Payment, flags: list[str]) -> str:
    if set(flags) & set(MISMATCH_FLAGS):
        return "mismatch"
    return {Payment.Status.SUCCEEDED: "paid", Payment.Status.PENDING: "pending"}.get(payment.status, "failed")


def payment_settlement(payment: Payment) -> dict:
    """Commission and the operator's net for one payment, in its own currency."""
    zero = Decimal("0.00")
    if payment.status != Payment.Status.SUCCEEDED:
        return {"refunded": zero, "commission": zero, "net_to_operator": zero}
    rate = payment.booking.trip.route.operator.commission_rate
    refunded = _succeeded_refunds_total(payment)
    commission = _quantize(payment.amount * rate / 100)
    return {
        "refunded": refunded,
        "commission": commission,
        "net_to_operator": payment.amount - refunded - commission,
    }


def operator_balances() -> list[dict]:
    """
    Per operator, per currency: what it has earned (succeeded payments -
    succeeded refunds - commission on gross, matching admin_reconciliation),
    what's been paid out, and what's still owed. Three grouped aggregates -
    KHR and USD are never added together.
    """
    from apps.operators.models import Operator

    gross = {
        (row["booking__trip__route__operator_id"], row["currency"]): row
        for row in Payment.objects.filter(status=Payment.Status.SUCCEEDED)
        .values("booking__trip__route__operator_id", "currency")
        .annotate(total=Sum("amount"), bookings=Count("booking", distinct=True))
    }
    # Refunds carry no currency of their own - group by their payment's.
    refunds = {
        (row["payment__booking__trip__route__operator_id"], row["payment__currency"]): row["total"]
        for row in Refund.objects.filter(status=Refund.Status.SUCCEEDED)
        .values("payment__booking__trip__route__operator_id", "payment__currency")
        .annotate(total=Sum("amount"))
    }
    payouts = {
        (row["operator_id"], row["currency"]): row
        for row in OperatorPayout.objects.values("operator_id", "currency").annotate(
            total=Sum("amount"), last_paid_at=Max("paid_at")
        )
    }
    keys = set(gross) | set(payouts)
    operators = {op.id: op for op in Operator.objects.filter(id__in={k[0] for k in keys})}
    zero = Decimal("0.00")
    rows = []
    for operator_id, currency in keys:
        operator = operators[operator_id]
        gross_row = gross.get((operator_id, currency), {})
        payout_row = payouts.get((operator_id, currency), {})
        gross_total = gross_row.get("total") or zero
        refunded = refunds.get((operator_id, currency)) or zero
        commission = _quantize(gross_total * operator.commission_rate / 100)
        earned = gross_total - refunded - commission
        paid_out = payout_row.get("total") or zero
        rows.append(
            {
                "operator_public_id": operator.public_id,
                "operator_name": operator.name,
                "currency": currency,
                "bookings_count": gross_row.get("bookings") or 0,
                "gross": gross_total,
                "refunded": refunded,
                "commission": commission,
                "earned": earned,
                "paid_out": paid_out,
                "outstanding": earned - paid_out,
                "last_paid_at": payout_row.get("last_paid_at"),
            }
        )
    return sorted(rows, key=lambda r: (r["operator_name"], r["currency"]))


class PayoutError(PaymentError):
    code = "payout_error"


@transaction.atomic
def record_payout(
    operator,
    *,
    currency: str,
    amount: Decimal,
    period_start,
    period_end,
    reference: str = "",
    note: str = "",
    actor,
) -> OperatorPayout:
    """Records that the platform transferred `amount` to the operator. Never
    more than what's actually outstanding in that currency."""
    amount = _quantize(amount)
    if period_end < period_start:
        raise PayoutError(_("The period end must not be before its start."))
    balance = next(
        (
            r
            for r in operator_balances()
            if r["operator_public_id"] == operator.public_id and r["currency"] == currency
        ),
        None,
    )
    outstanding = balance["outstanding"] if balance else Decimal("0.00")
    if amount <= 0 or amount > outstanding:
        raise PayoutError(
            _("Payout must be between 0 and the %(outstanding)s %(currency)s outstanding.")
            % {"outstanding": outstanding, "currency": currency}
        )
    payout = OperatorPayout.objects.create(
        operator=operator,
        currency=currency,
        amount=amount,
        period_start=period_start,
        period_end=period_end,
        reference=reference,
        note=note,
        paid_at=timezone.now(),
        paid_by=actor,
    )
    log_action(
        actor=actor,
        action=AuditLog.Action.PAYOUT_RECORDED,
        target=payout,
        before={"outstanding": str(outstanding)},
        after={"amount": str(amount), "currency": currency, "reference": reference},
    )
    return payout


def _by_currency(qs) -> dict:
    totals = {row["currency"]: row["total"] for row in qs.values("currency").annotate(total=Sum("amount"))}
    return {"usd": totals.get("USD") or Decimal("0.00"), "khr": totals.get("KHR") or Decimal("0.00")}


def reconciliation_summary(*, now=None) -> dict:
    """A3 info boxes: collected today, paid out, pending payout, mismatches."""
    now = now or timezone.now()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min), tz)
    pending = {"usd": Decimal("0.00"), "khr": Decimal("0.00")}
    for row in operator_balances():
        if row["outstanding"] > 0:
            pending[row["currency"].lower()] += row["outstanding"]
    collected = Payment.objects.filter(
        status=Payment.Status.SUCCEEDED, created_at__gte=start, created_at__lt=start + timedelta(days=1)
    )
    return {
        "collected_today": _by_currency(collected),
        "paid_out": _by_currency(OperatorPayout.objects.all()),
        "pending_payout": pending,
        "mismatch_count": Payment.objects.filter(mismatch_q()).count(),
    }
