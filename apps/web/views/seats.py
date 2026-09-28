from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.fleet.models import Seat
from apps.trips import services as trip_services
from apps.trips.models import Trip, TripSeat
from apps.web.forms import SeatSelectionForm
from apps.web.session import clear_checkout_hold, get_checkout_hold, set_checkout_hold


def _caller_identity(request):
    """Authenticated user, or the guest's session key (created if needed) —
    mirrors apps.trips.views._caller_identity for the DRF hold/release
    endpoints; kept as a small separate copy since the two view layers are
    intentionally decoupled (see the build plan)."""
    if request.user.is_authenticated:
        return request.user, None
    if not request.session.session_key:
        request.session.create()
    return None, request.session.session_key


def _trip_or_404(public_id):
    return get_object_or_404(
        Trip.objects.select_related(
            "route",
            "route__origin_city",
            "route__destination_city",
            "route__operator",
            "bus",
            "bus__seat_layout",
        ),
        public_id=public_id,
    )


def seat_selection_view(request, public_id):
    trip = _trip_or_404(public_id)

    if request.method == "POST":
        form = SeatSelectionForm(request.POST)
        if form.is_valid():
            seat_public_ids = form.cleaned_data["seat_ids"]

            try:
                trip_services.ensure_trip_bookable(trip)
            except (trip_services.TripNotBookableError, trip_services.TripSeatLayoutDesyncedError) as exc:
                messages.error(request, str(exc))
                return redirect("web:seat_selection", public_id=trip.public_id)

            seat_map = dict(
                Seat.objects.filter(
                    public_id__in=seat_public_ids, seat_layout=trip.bus.seat_layout_id
                ).values_list("public_id", "id")
            )
            missing = set(seat_public_ids) - set(str(k) for k in seat_map.keys())
            if missing:
                messages.error(request, _("Some selected seats are no longer available."))
                return redirect("web:seat_selection", public_id=trip.public_id)

            user, session_key = _caller_identity(request)
            try:
                token = trip_services.hold_seats(
                    trip.id, list(seat_map.values()), user=user, session_key=session_key
                )
            except (trip_services.TripLockedError, trip_services.SeatsUnavailableError) as exc:
                messages.error(request, str(exc))
                return redirect("web:seat_selection", public_id=trip.public_id)

            payload = trip_services.verify_hold_token(token)
            set_checkout_hold(
                request,
                trip_public_id=str(trip.public_id),
                hold_token=token,
                seat_ids=[str(sid) for sid in seat_public_ids],
                held_until=payload["held_until"],
            )
            return redirect("web:boarding_stops")
    else:
        form = SeatSelectionForm()

    trip_seats = (
        TripSeat.objects.filter(trip=trip)
        .select_related("seat")
        .order_by("seat__deck", "seat__row_position", "seat__col_position")
    )

    context = {"trip": trip, "trip_seats": trip_seats, "form": form}
    return render(request, "web/seats.html", context)


def release_hold_view(request):
    """"Change seats": releases whatever this session currently holds (if
    anything) and sends the passenger back to search. Expired holds are
    also swept server-side (trips.services.release_expired_holds, Celery),
    so this is only needed for an explicit change of mind."""
    hold = get_checkout_hold(request)
    if hold:
        trip = get_object_or_404(Trip, public_id=hold["trip_public_id"])
        seat_ids = list(Seat.objects.filter(public_id__in=hold["seat_ids"]).values_list("id", flat=True))
        user, session_key = _caller_identity(request)
        trip_services.release_seats(trip.id, seat_ids, user=user, session_key=session_key)
        clear_checkout_hold(request)
        return redirect("web:seat_selection", public_id=trip.public_id)

    return redirect("web:home")
