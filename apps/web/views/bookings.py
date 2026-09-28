from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.bookings import services as booking_services
from apps.bookings.models import Booking
from apps.bookings.services import find_guest_booking
from apps.trips import services as trip_services
from apps.trips.models import Trip
from apps.web.forms import CancelBookingForm, GuestBookingLookupForm, RescheduleDateForm


def my_bookings_view(request):
    if not request.user.is_authenticated:
        form = GuestBookingLookupForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            booking = find_guest_booking(form.cleaned_data["pnr"], form.cleaned_data["phone"])
            if booking is None:
                form.add_error(None, _("No booking matches that PNR and phone."))
            else:
                url = reverse("web:booking_detail", args=[booking.public_id])
                query = urlencode({"pnr": booking.pnr, "phone": booking.contact_phone})
                return redirect(f"{url}?{query}")
        return render(request, "web/bookings/my_bookings.html", {"guest_form": form})

    when = request.GET.get("when", "upcoming")
    now = timezone.now()
    qs = (
        Booking.objects.filter(user=request.user)
        .select_related("trip__route__origin_city", "trip__route__destination_city")
        .order_by("-created_at")
    )
    if when == "past":
        qs = qs.filter(Q(trip__departure_at__lt=now) | Q(status=Booking.Status.CANCELLED))
    else:
        qs = qs.filter(trip__departure_at__gte=now).exclude(status=Booking.Status.CANCELLED)

    return render(request, "web/bookings/my_bookings.html", {"bookings": qs, "when": when})


def booking_detail_view(request, public_id):
    if request.user.is_authenticated:
        booking = get_object_or_404(
            Booking.objects.select_related("trip__route__origin_city", "trip__route__destination_city"),
            public_id=public_id,
            user=request.user,
        )
    else:
        # A guest reaches here only right after a successful PNR+phone
        # lookup (my_bookings_view) — no ownership check is possible without
        # an account, so re-require the same lookup on every direct visit.
        booking = get_object_or_404(
            Booking.objects.select_related("trip__route__origin_city", "trip__route__destination_city"),
            public_id=public_id,
        )
        pnr = request.GET.get("pnr")
        phone = request.GET.get("phone")
        if pnr != booking.pnr or phone != booking.contact_phone:
            return redirect("web:my_bookings")

    passengers = booking.passengers.select_related("trip_seat__seat").order_by("id")

    # Cancel/reschedule mirror the DRF endpoints' own scoping (IsAuthenticated
    # + booking.user == request.user) — a guest booking has no account to
    # authenticate as, so it can only ever be viewed, never mutated here.
    can_manage = request.user.is_authenticated and booking.user_id == request.user.id

    cancel_form = CancelBookingForm()
    reschedule_form = RescheduleDateForm()
    refund_preview = None
    candidate_trips = None

    if can_manage and booking.status == Booking.Status.CONFIRMED:
        refund_percentage, refund_amount = booking_services.estimate_cancellation_refund(booking)
        refund_preview = {"percentage": refund_percentage, "amount": refund_amount}

    if can_manage and request.method == "POST":
        action = request.POST.get("action")

        if action == "cancel":
            cancel_form = CancelBookingForm(request.POST)
            if cancel_form.is_valid():
                try:
                    booking_services.cancel_booking(
                        booking, reason=cancel_form.cleaned_data["reason"], actor=request.user
                    )
                except booking_services.BookingStateError as exc:
                    cancel_form.add_error(None, str(exc))
                else:
                    messages.success(request, _("Booking cancelled."))
                    return redirect("web:booking_detail", public_id=booking.public_id)

        elif action == "search_reschedule":
            reschedule_form = RescheduleDateForm(request.POST)
            if reschedule_form.is_valid():
                candidate_trips = trip_services.search_trips(
                    origin=str(booking.trip.route.origin_city.public_id),
                    destination=str(booking.trip.route.destination_city.public_id),
                    date=reschedule_form.cleaned_data["date"],
                ).exclude(pk=booking.trip_id)

        elif action == "reschedule":
            new_trip_public_id = request.POST.get("new_trip_id")
            seat_ids = list(passengers.values_list("trip_seat__seat_id", flat=True))
            try:
                try:
                    new_trip = Trip.objects.get(public_id=new_trip_public_id)
                except Trip.DoesNotExist:
                    raise trip_services.TripNotBookableError(_("That trip is no longer available.")) from None
                trip_services.ensure_trip_bookable(new_trip)
                available_seats = list(
                    new_trip.trip_seats.filter(status="available").values_list("seat_id", flat=True)[
                        : len(seat_ids)
                    ]
                )
                if len(available_seats) < len(seat_ids):
                    raise trip_services.SeatsUnavailableError(available_seats)
                token = trip_services.hold_seats(new_trip.id, available_seats, user=request.user)
                booking_services.reschedule_booking(booking, new_trip.id, token)
            except (
                trip_services.TripNotBookableError,
                trip_services.TripLockedError,
                trip_services.SeatsUnavailableError,
                trip_services.InvalidHoldTokenError,
                booking_services.BookingStateError,
                booking_services.InvalidPassengerCountError,
            ) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Booking rescheduled."))
                return redirect("web:booking_detail", public_id=booking.public_id)

    context = {
        "booking": booking,
        "passengers": passengers,
        "can_manage": can_manage,
        "cancel_form": cancel_form,
        "reschedule_form": reschedule_form,
        "refund_preview": refund_preview,
        "candidate_trips": candidate_trips,
    }
    return render(request, "web/bookings/booking_detail.html", context)
