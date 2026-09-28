from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.test import RequestFactory
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.bookings.models import Booking
from apps.bookings.services import find_guest_booking
from apps.payments import services as payment_services
from apps.payments.gateways.mock import sign_mock_payload
from apps.payments.models import Payment


def _redirect_to_self(pnr, phone):
    url = reverse("web:payment_result")
    return redirect(f"{url}?{urlencode({'pnr': pnr, 'phone': phone})}")


def _ticket_redirect(pnr, phone):
    url = reverse("web:ticket")
    return redirect(f"{url}?{urlencode({'pnr': pnr, 'phone': phone})}")


def payment_result_view(request):
    pnr = request.GET.get("pnr", "")
    phone = request.GET.get("phone", "")
    booking = find_guest_booking(pnr, phone) if pnr and phone else None

    if booking is None:
        return render(request, "web/payment_result.html", {"pnr": pnr, "phone": phone, "booking": None})

    payment = booking.payments.order_by("-created_at").first()

    if request.method == "POST" and payment and payment.status == Payment.Status.PENDING:
        outcome = request.POST.get("outcome")
        if outcome in ("succeeded", "failed") and payment.provider == Payment.Provider.MOCK:
            payload = {
                "merchant_ref": str(payment.public_id),
                "status": outcome,
                "provider_txn_id": f"mock-sim-{payment.public_id.hex[:12]}",
            }
            body, signature = sign_mock_payload(payload)
            fake_request = RequestFactory().post(
                "/api/v1/payments/webhook/mock/", data=body, content_type="application/json"
            )
            fake_request.META["HTTP_X_MOCK_SIGNATURE"] = signature
            payment_services.process_webhook("mock", fake_request)
            messages.success(
                request,
                _("Payment succeeded.") if outcome == "succeeded" else _("Payment failed."),
            )
        return _redirect_to_self(pnr, phone)

    if booking.status == Booking.Status.CONFIRMED:
        return _ticket_redirect(pnr, phone)

    context = {"pnr": pnr, "phone": phone, "booking": booking, "payment": payment}
    return render(request, "web/payment_result.html", context)
