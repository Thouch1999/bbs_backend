"""Small request.session helpers for state that isn't a domain model —
display-currency preference and (later) in-progress checkout state."""

DISPLAY_CURRENCY_SESSION_KEY = "bbms_display_currency"
DEFAULT_DISPLAY_CURRENCY = "KHR"


def get_display_currency(request) -> str:
    if request.user.is_authenticated and getattr(request.user, "preferred_currency", None):
        return request.session.get(DISPLAY_CURRENCY_SESSION_KEY, request.user.preferred_currency)
    return request.session.get(DISPLAY_CURRENCY_SESSION_KEY, DEFAULT_DISPLAY_CURRENCY)


def set_display_currency(request, currency: str) -> None:
    request.session[DISPLAY_CURRENCY_SESSION_KEY] = currency


CHECKOUT_SESSION_KEY = "bbms_checkout"


def set_checkout_hold(
    request, *, trip_public_id: str, hold_token: str, seat_ids: list[str], held_until: str
) -> None:
    """`trip_public_id`/`seat_ids` are public_ids (strings); `held_until` is an ISO
    timestamp string. This is UI-flow state only — the hold's real source of
    truth is the signed hold_token itself (trips.services.verify_hold_token)."""
    request.session[CHECKOUT_SESSION_KEY] = {
        "trip_public_id": trip_public_id,
        "hold_token": hold_token,
        "seat_ids": seat_ids,
        "held_until": held_until,
    }


def get_checkout_hold(request) -> dict | None:
    return request.session.get(CHECKOUT_SESSION_KEY)


def clear_checkout_hold(request) -> None:
    request.session.pop(CHECKOUT_SESSION_KEY, None)
