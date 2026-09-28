from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from apps.accounts.models import User
from apps.web.forms import TripSearchForm
from apps.web.session import set_display_currency


def home_view(request):
    return render(request, "web/home.html", {"search_form": TripSearchForm()})


def set_currency_view(request):
    """POST-only: sets the viewer's display currency for the session (and,
    kept fully separate from the DRF API's preferred_currency field)."""
    if request.method == "POST":
        currency = request.POST.get("currency")
        if currency in dict(User.Currency.choices):
            set_display_currency(request, currency)
    next_url = request.POST.get("next", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"
    return HttpResponseRedirect(next_url)
