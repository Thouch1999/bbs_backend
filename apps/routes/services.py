"""Route stop-list editing. Business logic only — views just translate HTTP
<-> these calls, per CLAUDE.md's hard rules."""

from django.db import transaction
from django.db.models import F

from .models import Route, RouteStop

# Existing rows are parked this far above any real sequence while the list is
# rewritten, so UNIQUE(route, sequence) never trips mid-reorder.
_SEQUENCE_PARKING_OFFSET = 1000


class RouteStopsError(Exception):
    code = "route_stops_error"


@transaction.atomic
def set_route_stops(route: Route, items: list[dict]) -> list[RouteStop]:
    """
    Replaces the route's ordered stop list with `items` (in order), each
    {"stop": Stop, "offset_minutes": int, "route_stop": RouteStop | None}.
    Existing RouteStops (matched by `route_stop`) are updated in place
    rather than deleted and recreated, because bookings reference them as
    boarding/drop points (on_delete=PROTECT) — so a reorder never breaks an
    existing booking. A removed stop that a booking still uses raises
    ProtectedError, which rolls the whole change back (409 via the API).
    """
    if not items:
        raise RouteStopsError("A route needs at least one stop.")

    stop_ids = [item["stop"].id for item in items]
    if len(set(stop_ids)) != len(stop_ids):
        raise RouteStopsError("A stop can only appear once on a route.")
    offsets = [item["offset_minutes"] for item in items]
    if offsets != sorted(offsets):
        raise RouteStopsError("Minutes from departure must not decrease along the route.")

    existing = {rs.id: rs for rs in route.route_stops.select_for_update()}
    for item in items:
        route_stop = item.get("route_stop")
        if route_stop is not None and route_stop.id not in existing:
            raise RouteStopsError("A stop in the list belongs to a different route.")

    kept_ids = {item["route_stop"].id for item in items if item.get("route_stop") is not None}
    for route_stop_id, route_stop in existing.items():
        if route_stop_id not in kept_ids:
            route_stop.delete()

    route.route_stops.update(sequence=F("sequence") + _SEQUENCE_PARKING_OFFSET)

    result = []
    for sequence, item in enumerate(items, start=1):
        route_stop = item.get("route_stop")
        if route_stop is None:
            route_stop = RouteStop(route=route)
        else:
            route_stop.refresh_from_db()
        route_stop.stop = item["stop"]
        route_stop.sequence = sequence
        route_stop.offset_minutes = item["offset_minutes"]
        route_stop.save()
        result.append(route_stop)
    return result
