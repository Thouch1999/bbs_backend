from urllib.parse import urlencode

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.bookings import services as booking_services
from apps.payments import services as payment_services
from apps.trips import services as trip_services
from apps.trips.models import Trip
from apps.web.forms import (
    CheckoutContactForm,
    PassengerDetailFormSet,
    PaymentMethodForm,
    PromoCodeForm,
)
from apps.web.session import clear_checkout_hold, get_checkout_hold, get_display_currency


def checkout_view(request):
    hold = get_checkout_hold(request)
    if not hold or "boarding_stop_id" not in hold:
        return redirect("web:home")

    trip = get_object_or_404(
        Trip.objects.select_related("route", "route__origin_city", "route__destination_city"),
        public_id=hold["trip_public_id"],
    )
    num_seats = len(hold["seat_ids"])
    currency = get_display_currency(request)
    subtotal = booking_services.estimate_subtotal(trip, currency, num_seats)

    if request.method == "POST":
        contact_form = CheckoutContactForm(request.POST)
        formset = PassengerDetailFormSet(request.POST, prefix="passengers")
        promo_form = PromoCodeForm(request.POST)
        payment_form = PaymentMethodForm(request.POST)

        forms_valid = (
            contact_form.is_valid()
            and formset.is_valid()
            and promo_form.is_valid()
            and payment_form.is_valid()
        )
        if forms_valid and len(formset.forms) != num_seats:
            forms_valid = False
            contact_form.add_error(
                None, _("Passenger details are missing for one or more held seats.")
            )

        discount = 0
        promo_code = promo_form.cleaned_data.get("code", "") if forms_valid else ""
        if forms_valid and promo_code:
            try:
                discount = payment_services.validate_promo_code(
                    promo_code,
                    subtotal=subtotal,
                    trip=trip,
                    user=request.user if request.user.is_authenticated else None,
                    contact_phone=contact_form.cleaned_data["contact_phone"],
                )
            except payment_services.PromoCodeError as exc:
                forms_valid = False
                promo_form.add_error("code", str(exc))

        if forms_valid:
            passengers = [f.cleaned_data for f in formset.forms]
            try:
                booking = booking_services.create_booking(
                    trip.id,
                    hold["hold_token"],
                    passengers,
                    contact_phone=contact_form.cleaned_data["contact_phone"],
                    contact_email=contact_form.cleaned_data.get("contact_email") or None,
                    user=request.user if request.user.is_authenticated else None,
                    currency=currency,
                    boarding_stop_id=hold["boarding_stop_id"],
                    drop_stop_id=hold["drop_stop_id"],
                    discount_amount=discount,
                    promo_code=promo_code,
                )
            except (
                trip_services.InvalidHoldTokenError,
                trip_services.SeatsUnavailableError,
                booking_services.InvalidPassengerCountError,
            ) as exc:
                clear_checkout_hold(request)
                contact_form.add_error(None, str(exc))
            else:
                payment_services.initiate_payment(booking, payment_form.cleaned_data["provider"])
                clear_checkout_hold(request)
                url = reverse("web:payment_result")
                query = urlencode({"pnr": booking.pnr, "phone": booking.contact_phone})
                return redirect(f"{url}?{query}")
    else:
        contact_form = CheckoutContactForm()
        formset = PassengerDetailFormSet(prefix="passengers", initial=[{} for _ in range(num_seats)])
        promo_form = PromoCodeForm()
        payment_form = PaymentMethodForm()

    context = {
        "trip": trip,
        "num_seats": num_seats,
        "subtotal": subtotal,
        "currency": currency,
        "contact_form": contact_form,
        "formset": formset,
        "promo_form": promo_form,
        "payment_form": payment_form,
    }
    return render(request, "web/checkout.html", context)
