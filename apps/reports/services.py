"""
Read-only aggregate reports (SRS 4.7/4.8, playbook Step 10). Everything here
does its summing/counting in SQL via annotate()/aggregate() — never in a
Python loop over raw rows — per the playbook's explicit instruction.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DateTimeField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.bookings.models import Booking
from apps.operators.models import Operator
from apps.payments.models import Payment, Refund
from apps.routes.models import Route
from apps.trips.models import Trip, TripSeat

_ZERO = Decimal("0.00")
_ACTIVE_BOOKING_STATUSES = (Booking.Status.CONFIRMED, Booking.Status.COMPLETED)


def _date_range_bounds(
    date_from: date | None, date_to: date | None, *, default_direction: str = "past"
) -> tuple[datetime, datetime]:
    """
    Turns an inclusive [date_from, date_to] (local calendar dates) into an
    aware [start, end) datetime range — a plain range comparison, not a
    __date extraction, so it can actually use the indexes from SRS 7.20
    instead of wrapping the column in a DB function.

    With no explicit dates, defaults to the last 30 days ("past" — revenue,
    cancellations, platform totals: reporting on what already happened) or
    the next 30 days ("future" — occupancy: usually checked for upcoming
    trips, not historical ones).
    """
    today = timezone.localdate()
    if default_direction == "future":
        date_from = date_from or today
        date_to = date_to or today + timedelta(days=30)
    else:
        date_from = date_from or today - timedelta(days=30)
        date_to = date_to or today
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end = timezone.make_aware(datetime.combine(date_to, time.min), tz) + timedelta(days=1)
    return start, end


_REVENUE_GROUP_FIELDS = {
    "route": {
        "route_public_id": "booking__trip__route__public_id",
        "route_name": "booking__trip__route__name",
    },
    "bus": {
        "bus_public_id": "booking__trip__bus__public_id",
        "plate_number": "booking__trip__bus__plate_number",
    },
    "payment_method": {"provider": "provider"},
    "channel": {"channel": "booking__booking_channel"},
}


def _local_date(field: str):
    """
    The local (Asia/Phnom_Penh) calendar date of a UTC datetime column,
    computed without CONVERT_TZ(): TruncDate(tzinfo=<local>) compiles to
    CONVERT_TZ on MySQL/MariaDB, which returns NULL unless the server's time
    zone tables are loaded (they aren't on this project's dev MariaDB). So
    shift by the zone's current UTC offset and truncate in UTC instead —
    exact for Asia/Phnom_Penh, which has no DST.
    """
    offset = timezone.localtime().utcoffset() or timedelta(0)
    shifted = ExpressionWrapper(F(field) + offset, output_field=DateTimeField())
    return TruncDate(shifted, tzinfo=UTC)


def _with_commission(rows: list[dict], commission_rate: Decimal) -> list[dict]:
    """Adds the platform's commission and the operator's net per row —
    per-row arithmetic on already-aggregated totals, not a loop over raw
    payments."""
    for row in rows:
        commission = (row["total_amount"] * commission_rate / 100).quantize(Decimal("0.01"))
        row["commission_amount"] = commission
        row["net_amount"] = row["total_amount"] - commission
    return rows


def operator_revenue(operator: Operator, *, date_from=None, date_to=None, group_by="route") -> list[dict]:
    """
    Succeeded payments, grouped by "route", "bus", "payment_method",
    "channel" (booking channel) or "day" (local payment date, per payment
    method — the daily stacked chart). Every row carries its `currency`:
    KHR and USD payments are summed separately and never added together
    (before Step 15 a KHR payment of 41000 and a USD payment of 10.00 were
    reported as one 41010.00 total).
    """
    start, end = _date_range_bounds(date_from, date_to)
    base = Payment.objects.filter(
        status=Payment.Status.SUCCEEDED,
        booking__trip__route__operator=operator,
        created_at__gte=start,
        created_at__lt=end,
    )
    aggregates = {"total_amount": Coalesce(Sum("amount"), _ZERO), "payment_count": Count("id")}

    if group_by == "day":
        rows = (
            base.annotate(date=_local_date("created_at"))
            .values("date", "provider", "currency")
            .annotate(**aggregates)
            .order_by("date", "provider", "currency")
        )
        return _with_commission(list(rows), operator.commission_rate)
    if group_by not in _REVENUE_GROUP_FIELDS:
        raise ValueError(f"Unknown group_by '{group_by}'.")

    fields = _REVENUE_GROUP_FIELDS[group_by]
    rows = (
        base.annotate(**{alias: F(path) for alias, path in fields.items() if alias != path})
        .values(*fields.keys(), "currency")
        .annotate(**aggregates)
        .order_by("currency", "-total_amount")
    )
    return _with_commission(list(rows), operator.commission_rate)


def operator_occupancy(operator: Operator, *, date_from=None, date_to=None) -> list[dict]:
    start, end = _date_range_bounds(date_from, date_to, default_direction="future")
    trips = (
        Trip.objects.filter(route__operator=operator, departure_at__gte=start, departure_at__lt=end)
        .exclude(status=Trip.Status.CANCELLED)
        .annotate(
            total_seats=Count("trip_seats", distinct=True),
            booked_seats=Count(
                "trip_seats", filter=Q(trip_seats__status=TripSeat.Status.BOOKED), distinct=True
            ),
            route_public_id=F("route__public_id"),
            route_name=F("route__name"),
        )
        .values("public_id", "departure_at", "route_public_id", "route_name", "total_seats", "booked_seats")
        .order_by("departure_at")
    )

    results = []
    for trip in trips:
        total, booked = trip["total_seats"] or 0, trip["booked_seats"] or 0
        rate = (Decimal(booked) / Decimal(total) * 100).quantize(Decimal("0.01")) if total else _ZERO
        results.append({**trip, "occupancy_rate": rate})
    return results


def operator_cancellation_summary(operator: Operator, *, date_from=None, date_to=None) -> dict:
    """Refund totals per currency (see operator_revenue — never mixed)."""
    start, end = _date_range_bounds(date_from, date_to)
    qs = Booking.objects.filter(
        trip__route__operator=operator,
        status=Booking.Status.CANCELLED,
        cancelled_at__gte=start,
        cancelled_at__lt=end,
    )
    return qs.aggregate(
        cancelled_count=Count("id"),
        total_refund_usd=Coalesce(Sum("refund_amount", filter=Q(currency=Booking.Currency.USD)), _ZERO),
        total_refund_khr=Coalesce(Sum("refund_amount", filter=Q(currency=Booking.Currency.KHR)), _ZERO),
    )


def _revenue_by_currency(payments) -> dict:
    rows = payments.values("currency").annotate(total=Sum("amount"))
    totals = {row["currency"]: row["total"] for row in rows}
    return {"usd": totals.get(Booking.Currency.USD, _ZERO), "khr": totals.get(Booking.Currency.KHR, _ZERO)}


def operator_dashboard(operator: Operator, *, now=None) -> dict:
    """
    One call for the operator home screen (O1): today's KPIs, delayed and
    departing-soon trips, the "needs attention" counts, a 14-day revenue
    series and the top routes. Every number is a SQL aggregate.
    """
    now = now or timezone.now()
    today = timezone.localdate(now)
    day_start, day_end = _date_range_bounds(today, today)
    trips = Trip.objects.filter(route__operator=operator)
    todays_trips = trips.filter(departure_at__gte=day_start, departure_at__lt=day_end).exclude(
        status=Trip.Status.CANCELLED
    )
    seat_totals = TripSeat.objects.filter(trip__in=todays_trips).aggregate(
        total=Count("id"), booked=Count("id", filter=Q(status=TripSeat.Status.BOOKED))
    )
    occupancy = (
        (Decimal(seat_totals["booked"]) / Decimal(seat_totals["total"]) * 100).quantize(Decimal("0.01"))
        if seat_totals["total"]
        else _ZERO
    )

    operator_payments = Payment.objects.filter(
        status=Payment.Status.SUCCEEDED, booking__trip__route__operator=operator
    )
    active_bookings = Booking.objects.filter(
        trip__route__operator=operator,
        status__in=(Booking.Status.PENDING_PAYMENT, *_ACTIVE_BOOKING_STATUSES),
    )

    upcoming = trips.filter(departure_at__gte=now, status__in=(Trip.Status.SCHEDULED, Trip.Status.DELAYED))
    series_start, series_end = _date_range_bounds(today - timedelta(days=13), today)
    top_start, top_end = _date_range_bounds(today - timedelta(days=29), today)

    def _trip_rows(qs, limit):
        return list(
            qs.annotate(route_name=F("route__name"), plate_number=F("bus__plate_number"))
            .values("public_id", "route_name", "plate_number", "departure_at", "status", "delay_minutes")
            .order_by("departure_at")[:limit]
        )

    expiry_cutoff = today + timedelta(days=30)
    return {
        "date": today,
        "trips_today": todays_trips.count(),
        "trips_remaining_today": todays_trips.filter(departure_at__gte=now).count(),
        "bookings_today": active_bookings.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
        "seats_sold_today": seat_totals["booked"],
        "seats_total_today": seat_totals["total"],
        "occupancy_rate_today": occupancy,
        "revenue_today": _revenue_by_currency(
            operator_payments.filter(created_at__gte=day_start, created_at__lt=day_end)
        ),
        "delayed_trips": _trip_rows(upcoming.filter(status=Trip.Status.DELAYED), 10),
        "departing_soon": _trip_rows(upcoming.filter(departure_at__lt=now + timedelta(hours=2)), 10),
        "attention": {
            "pending_counter_payments": Booking.objects.filter(
                trip__route__operator=operator,
                status=Booking.Status.PENDING_PAYMENT,
                payments__provider=Payment.Provider.COUNTER,
                payments__status=Payment.Status.PENDING,
            )
            .distinct()
            .count(),
            "refunds_to_process": Booking.objects.filter(
                trip__route__operator=operator,
                status=Booking.Status.CANCELLED,
                refund_amount__gt=0,
                payments__status=Payment.Status.SUCCEEDED,
                payments__refunds__isnull=True,
            )
            .distinct()
            .count(),
            "unassigned_trips": upcoming.filter(departure_at__lt=now + timedelta(days=7))
            .filter(Q(driver_name="") | Q(conductor__isnull=True))
            .count(),
            "expiring_bus_documents": operator.buses.exclude(status="retired")
            .filter(
                Q(registration_expires_on__lte=expiry_cutoff) | Q(insurance_expires_on__lte=expiry_cutoff)
            )
            .count(),
        },
        "revenue_series": list(
            operator_payments.filter(created_at__gte=series_start, created_at__lt=series_end)
            .annotate(date=_local_date("created_at"))
            .values("date", "currency")
            .annotate(total_amount=Coalesce(Sum("amount"), _ZERO))
            .order_by("date", "currency")
        ),
        # Ranked by seats sold, not revenue: a KHR total and a USD total
        # aren't comparable without picking an exchange rate.
        "top_routes": list(
            Route.objects.filter(operator=operator)
            .annotate(
                seats_sold=Count(
                    "trips__trip_seats",
                    filter=Q(
                        trips__trip_seats__status=TripSeat.Status.BOOKED,
                        trips__departure_at__gte=top_start,
                        trips__departure_at__lt=top_end,
                    ),
                )
            )
            .filter(seats_sold__gt=0)
            .values("public_id", "name", "seats_sold")
            .order_by("-seats_sold")[:5]
        ),
    }


def _per_currency(qs, field: str) -> dict:
    """{"usd": ..., "khr": ...} sums of `field`, grouped by the row's currency
    — KHR and USD amounts are never added together."""
    totals = {row["currency"]: row["total"] for row in qs.values("currency").annotate(total=Sum(field))}
    return {"usd": totals.get("USD") or _ZERO, "khr": totals.get("KHR") or _ZERO}


def admin_platform_totals(*, date_from=None, date_to=None) -> dict:
    """
    Platform-wide KPIs for a period (bookings created in it). Revenue and
    commission are per currency; before Step 16 total_revenue summed KHR
    and USD booking totals into one number.
    """
    start, end = _date_range_bounds(date_from, date_to)
    qs = Booking.objects.filter(
        status__in=_ACTIVE_BOOKING_STATUSES, created_at__gte=start, created_at__lt=end
    )
    # Separate aggregate() calls: Count("passengers") joins to
    # BookingPassenger, which would fan out the revenue sums.
    counts = qs.aggregate(total_bookings=Count("id", distinct=True))
    counts["total_passengers"] = qs.aggregate(n=Count("passengers", distinct=True))["n"]

    commission = {"usd": _ZERO, "khr": _ZERO}
    for row in qs.values("currency", "trip__route__operator__commission_rate").annotate(
        total=Sum("total_amount")
    ):
        key = row["currency"].lower()
        commission[key] += (row["total"] * row["trip__route__operator__commission_rate"] / 100).quantize(
            Decimal("0.01")
        )

    period_all = Booking.objects.filter(created_at__gte=start, created_at__lt=end)
    cancelled = period_all.filter(status=Booking.Status.CANCELLED).count()
    decided = period_all.exclude(status=Booking.Status.PENDING_PAYMENT).count()

    trip_seats = TripSeat.objects.filter(trip__departure_at__gte=start, trip__departure_at__lt=end).exclude(
        trip__status=Trip.Status.CANCELLED
    )
    seat_totals = trip_seats.aggregate(
        total=Count("id"), booked=Count("id", filter=Q(status=TripSeat.Status.BOOKED))
    )
    return {
        **counts,
        "revenue": _per_currency(qs, "total_amount"),
        "commission": commission,
        "active_operators": Operator.objects.filter(status=Operator.Status.APPROVED).count(),
        "average_occupancy": (
            (Decimal(seat_totals["booked"]) / Decimal(seat_totals["total"]) * 100).quantize(Decimal("0.01"))
            if seat_totals["total"]
            else _ZERO
        ),
        "cancellation_rate": (
            (Decimal(cancelled) / Decimal(decided) * 100).quantize(Decimal("0.01")) if decided else _ZERO
        ),
    }


def admin_top_routes(*, date_from=None, date_to=None, limit=10) -> list[dict]:
    """
    Top platform routes — a city pair across every operator that runs it —
    by bookings. Revenue per currency (never summed across currencies).
    """
    start, end = _date_range_bounds(date_from, date_to)
    bookings = Booking.objects.filter(
        status__in=_ACTIVE_BOOKING_STATUSES, created_at__gte=start, created_at__lt=end
    )
    city_pair = ("trip__route__origin_city_id", "trip__route__destination_city_id")
    ranked = list(
        bookings.values(*city_pair)
        .annotate(
            bookings_count=Count("id", distinct=True),
            operators_count=Count("trip__route__operator", distinct=True),
            origin_city_name=F("trip__route__origin_city__name_en"),
            origin_city_name_km=F("trip__route__origin_city__name_km"),
            destination_city_name=F("trip__route__destination_city__name_en"),
            destination_city_name_km=F("trip__route__destination_city__name_km"),
        )
        .order_by("-bookings_count")[:limit]
    )
    revenue = {}
    for row in bookings.values(*city_pair, "currency").annotate(total=Sum("total_amount")):
        revenue[(row[city_pair[0]], row[city_pair[1]], row["currency"])] = row["total"]
    return [
        {
            "origin_city_name": row["origin_city_name"],
            "origin_city_name_km": row["origin_city_name_km"],
            "destination_city_name": row["destination_city_name"],
            "destination_city_name_km": row["destination_city_name_km"],
            "bookings_count": row["bookings_count"],
            "operators_count": row["operators_count"],
            "revenue_usd": revenue.get((row[city_pair[0]], row[city_pair[1]], "USD"), _ZERO),
            "revenue_khr": revenue.get((row[city_pair[0]], row[city_pair[1]], "KHR"), _ZERO),
        }
        for row in ranked
    ]


def admin_operator_summary(*, date_from=None, date_to=None) -> list[dict]:
    """Per operator: bookings, seats sold/occupancy on trips departing in the
    period, and revenue per currency — the platform reports table."""
    start, end = _date_range_bounds(date_from, date_to)
    bookings = Booking.objects.filter(
        status__in=_ACTIVE_BOOKING_STATUSES, created_at__gte=start, created_at__lt=end
    )
    counts = {
        row["trip__route__operator_id"]: row["n"]
        for row in bookings.values("trip__route__operator_id").annotate(n=Count("id", distinct=True))
    }
    revenue = {}
    for row in bookings.values("trip__route__operator_id", "currency").annotate(total=Sum("total_amount")):
        revenue[(row["trip__route__operator_id"], row["currency"])] = row["total"]
    seats = {
        row["trip__route__operator_id"]: row
        for row in TripSeat.objects.filter(trip__departure_at__gte=start, trip__departure_at__lt=end)
        .exclude(trip__status=Trip.Status.CANCELLED)
        .values("trip__route__operator_id")
        .annotate(total=Count("id"), booked=Count("id", filter=Q(status=TripSeat.Status.BOOKED)))
    }
    rows = []
    for operator in Operator.objects.filter(id__in=set(counts) | set(seats)).order_by("name"):
        seat_row = seats.get(operator.id, {"total": 0, "booked": 0})
        rows.append(
            {
                "operator_public_id": operator.public_id,
                "operator_name": operator.name,
                "bookings_count": counts.get(operator.id, 0),
                "seats_sold": seat_row["booked"],
                "seats_total": seat_row["total"],
                "occupancy_rate": (
                    (Decimal(seat_row["booked"]) / Decimal(seat_row["total"]) * 100).quantize(Decimal("0.01"))
                    if seat_row["total"]
                    else _ZERO
                ),
                "revenue_usd": revenue.get((operator.id, "USD"), _ZERO),
                "revenue_khr": revenue.get((operator.id, "KHR"), _ZERO),
            }
        )
    return rows


def admin_daily_series(*, date_from=None, date_to=None) -> list[dict]:
    """Bookings count and revenue per currency per local day (A1 chart)."""
    start, end = _date_range_bounds(date_from, date_to)
    bookings = Booking.objects.filter(
        status__in=_ACTIVE_BOOKING_STATUSES, created_at__gte=start, created_at__lt=end
    ).annotate(day=_local_date("created_at"))
    by_day = {}
    for row in bookings.values("day", "currency").annotate(n=Count("id"), total=Sum("total_amount")):
        entry = by_day.setdefault(
            row["day"], {"date": row["day"], "bookings": 0, "revenue_usd": _ZERO, "revenue_khr": _ZERO}
        )
        entry["bookings"] += row["n"]
        entry[f"revenue_{row['currency'].lower()}"] += row["total"]
    return [by_day[day] for day in sorted(by_day)]


def admin_reconciliation(*, date_from=None, date_to=None) -> list[dict]:
    """
    Per operator AND currency: gross revenue recorded, refunds paid out, and
    what's owed to the operator after commission. "Gateway settled" here means
    net of refunds from our own Payment/Refund records — the real gateways (ABA
    PayWay/Wing/ACLEDA) are TODO stubs with no settlement API to reconcile
    against yet (see apps/payments/gateways/*.py); this is the best available
    source until one is wired up.

    Grouped by currency since Step 16 (it used to add KHR and USD together).
    Deliberately separately-grouped queries, not one annotate() walking
    Operator -> routes -> trips -> bookings -> payments -> refunds: Django
    fans out multi-valued joins, so summing both Payment.amount and
    Refund.amount over that single path in one query would double-count.
    """
    start, end = _date_range_bounds(date_from, date_to)

    gross = {
        (row["booking__trip__route__operator_id"], row["currency"]): row["gross"]
        for row in (
            Payment.objects.filter(status=Payment.Status.SUCCEEDED, created_at__gte=start, created_at__lt=end)
            .values("booking__trip__route__operator_id", "currency")
            .annotate(gross=Coalesce(Sum("amount"), _ZERO))
        )
    }
    refunded = {
        (row["payment__booking__trip__route__operator_id"], row["payment__currency"]): row["refunded"]
        for row in (
            Refund.objects.filter(status=Refund.Status.SUCCEEDED, created_at__gte=start, created_at__lt=end)
            .values("payment__booking__trip__route__operator_id", "payment__currency")
            .annotate(refunded=Coalesce(Sum("amount"), _ZERO))
        )
    }

    keys = set(gross) | set(refunded)
    operators = {op.id: op for op in Operator.objects.filter(id__in={k[0] for k in keys})}
    rows = []
    for operator_id, currency in keys:
        operator = operators[operator_id]
        gross_amount = gross.get((operator_id, currency), _ZERO)
        refund_amount = refunded.get((operator_id, currency), _ZERO)
        commission = (gross_amount * operator.commission_rate / 100).quantize(Decimal("0.01"))
        rows.append(
            {
                "operator_public_id": operator.public_id,
                "operator_name": operator.name,
                "currency": currency,
                "gateway_settled": gross_amount - refund_amount,
                "platform_recorded": gross_amount,
                "refunded": refund_amount,
                "commission_amount": commission,
                "operator_payable": (gross_amount - refund_amount - commission).quantize(Decimal("0.01")),
            }
        )
    return sorted(rows, key=lambda row: (row["operator_name"], row["currency"]))


def system_status(*, now=None) -> list[dict]:
    """
    A1 "system status" pills, from what's actually configured plus recent
    traffic — never a pretend health check. mode: "live", "test" (mock
    gateway), "console" (dev: logged, not delivered) or "not_implemented"
    (a real integration still stubbed out).
    """
    from django.conf import settings

    from apps.notifications.models import Notification
    from apps.payments.models import WebhookLog

    now = now or timezone.now()
    day_ago = now - timedelta(hours=24)
    rows = []
    gateway_modes = {
        "aba_payway": "not_implemented",
        "wing": "not_implemented",
        "acleda": "not_implemented",
        "counter": "live",
        "mock": "test",
    }
    for provider, label in Payment.Provider.choices:
        payments = Payment.objects.filter(provider=provider)
        rows.append(
            {
                "key": provider,
                "kind": "payment",
                "label": label,
                "mode": gateway_modes.get(provider, "not_implemented"),
                "last_success_at": payments.filter(status=Payment.Status.SUCCEEDED).aggregate(
                    t=Max("updated_at")
                )["t"],
                "last_webhook_at": WebhookLog.objects.filter(provider=provider).aggregate(
                    t=Max("created_at")
                )["t"],
                "failures_24h": payments.filter(
                    status=Payment.Status.FAILED, updated_at__gte=day_ago
                ).count(),
                "pending": payments.filter(status=Payment.Status.PENDING).count(),
            }
        )
    channel_modes = {
        "sms": "console" if settings.SMS_PROVIDER == "console" else "not_implemented",
        "telegram": "console" if not settings.TELEGRAM_BOT_TOKEN else "not_implemented",
        "email": "console"
        if "console" in settings.EMAIL_BACKEND or "locmem" in settings.EMAIL_BACKEND
        else "live",
    }
    for channel, label in Notification.Channel.choices:
        notifications = Notification.objects.filter(channel=channel)
        rows.append(
            {
                "key": channel,
                "kind": "notification",
                "label": label,
                "mode": channel_modes[channel],
                "last_success_at": notifications.filter(status=Notification.Status.SENT).aggregate(
                    t=Max("sent_at")
                )["t"],
                "last_webhook_at": None,
                "failures_24h": notifications.filter(
                    status=Notification.Status.FAILED, updated_at__gte=day_ago
                ).count(),
                "pending": notifications.filter(
                    status=Notification.Status.PENDING, created_at__lt=now - timedelta(minutes=10)
                ).count(),
            }
        )
    for row in rows:
        row["health"] = (
            "down"
            if row["mode"] == "not_implemented"
            else "degraded"
            if row["failures_24h"] or row["pending"]
            else "ok"
        )
    return rows


def admin_overview(*, now=None) -> dict:
    """A1 in one call: 30-day KPIs, pending applications, daily series, top
    routes and system status."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    date_from = today - timedelta(days=29)
    return {
        "totals": admin_platform_totals(date_from=date_from, date_to=today),
        "pending_operators": list(
            Operator.objects.filter(status=Operator.Status.PENDING)
            .order_by("created_at")
            .values("public_id", "name", "name_km", "licence_number", "licence_document", "created_at")[:10]
        ),
        "pending_operators_count": Operator.objects.filter(status=Operator.Status.PENDING).count(),
        "series": admin_daily_series(date_from=date_from, date_to=today),
        "top_routes": admin_top_routes(date_from=date_from, date_to=today, limit=10),
        "system_status": system_status(now=now),
    }
