from datetime import datetime

from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from apps.trips import services as trip_services
from apps.web.forms import TripSearchForm

PAGE_SIZE = 10

SORT_OPTIONS = {
    "departure": "departure_at",
    "price_asc": "base_fare_usd",
    "price_desc": "-base_fare_usd",
}


def search_results_view(request):
    params = request.GET
    origin = params.get("origin")
    destination = params.get("destination")
    date_str = params.get("date")
    date = None
    if date_str:
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            date = None
    if date is None:
        date = timezone.localdate()

    trips = trip_services.search_trips(
        origin=origin,
        destination=destination,
        date=date,
        bus_type=params.get("bus_type") or None,
        operator=params.get("operator") or None,
    )

    sort = params.get("sort", "departure")
    trips = trips.order_by(SORT_OPTIONS.get(sort, "departure_at"))

    paginator = Paginator(trips, PAGE_SIZE)
    page_obj = paginator.get_page(params.get("page"))

    search_form = TripSearchForm(
        initial={"origin": origin, "destination": destination, "date": date}
    )

    context = {
        "page_obj": page_obj,
        "sort": sort,
        "search_form": search_form,
        "origin": origin,
        "destination": destination,
        "date": date,
    }
    return render(request, "web/search_results.html", context)
