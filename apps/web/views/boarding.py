from django.shortcuts import get_object_or_404, redirect, render

from apps.routes.models import RouteStop
from apps.trips.models import Trip
from apps.web.forms import BoardingStopForm
from apps.web.session import get_checkout_hold


def boarding_stop_view(request):
    hold = get_checkout_hold(request)
    if not hold:
        return redirect("web:home")
    trip = get_object_or_404(
        Trip.objects.select_related("route", "route__origin_city", "route__destination_city"),
        public_id=hold["trip_public_id"],
    )
    route_stops = RouteStop.objects.filter(route_id=trip.route_id).select_related("stop", "stop__city")

    if request.method == "POST":
        form = BoardingStopForm(request.POST, route_stops=route_stops)
        if form.is_valid():
            hold["boarding_stop_id"] = form.cleaned_data["boarding_stop"].id
            hold["drop_stop_id"] = form.cleaned_data["drop_stop"].id
            request.session.modified = True
            return redirect("web:checkout")
    else:
        form = BoardingStopForm(route_stops=route_stops)

    return render(request, "web/boarding_stops.html", {"trip": trip, "form": form})
