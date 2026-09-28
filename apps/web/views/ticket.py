from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.translation import get_language

from apps.bookings.services import find_guest_booking
from apps.notifications.eticket import generate_ticket_pdf
from apps.web.qr import qr_data_uri


def _find_booking_or_404(request):
    pnr = request.GET.get("pnr", "")
    phone = request.GET.get("phone", "")
    booking = find_guest_booking(pnr, phone) if pnr and phone else None
    if booking is None:
        raise Http404("No booking matches that PNR and phone.")
    return booking


def ticket_view(request):
    booking = _find_booking_or_404(request)
    passengers = booking.passengers.select_related("trip_seat__seat").order_by("id")
    context = {
        "booking": booking,
        "passengers": passengers,
        "qr_src": qr_data_uri(booking.qr_token or booking.pnr),
    }
    return render(request, "web/ticket.html", context)


def ticket_pdf_view(request):
    booking = _find_booking_or_404(request)
    language = get_language() if get_language() in ("km", "en") else "km"
    pdf_bytes = generate_ticket_pdf(booking, language=language)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{booking.pnr}.pdf"'
    return response
