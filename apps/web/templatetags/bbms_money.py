from django import template

register = template.Library()


@register.filter
def money(amount, currency: str) -> str:
    """Pure formatting — never converts between currencies. `amount` is a
    DECIMAL already in `currency` (e.g. Trip.base_fare_usd/base_fare_khr)."""
    if amount is None:
        return ""
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{int(amount):,} KHR"
