"""
The e-ticket PDF: PNR + QR code, kept intentionally light (SRS 5: low-bandwidth
users) — one small page, no photos, a single embedded font. Khmer rendering
uses apps.core.pdf_fonts (Kantumruy Pro), shared with the manifest exports.
"""

import io

from django.utils import timezone
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from apps.core.pdf_fonts import BOLD_FONT as _BOLD_FONT
from apps.core.pdf_fonts import REGULAR_FONT as _REGULAR_FONT
from apps.core.pdf_fonts import register_kantumruy_pro
from apps.core.utils import qr_png_bytes

_LABELS = {
    "km": {
        "title": "សំបុត្រអេឡិចត្រូនិក",
        "pnr": "លេខកក់",
        "route": "ផ្លូវ",
        "departure": "ម៉ោងចេញដំណើរ",
        "passengers": "អ្នកដំណើរ",
        "seat": "កៅអី",
        "total": "សរុប",
        "footer": "សូមបង្ហាញកូដ QR នេះនៅពេលឡើងឡាន",
    },
    "en": {
        "title": "E-Ticket",
        "pnr": "PNR",
        "route": "Route",
        "departure": "Departure",
        "passengers": "Passengers",
        "seat": "Seat",
        "total": "Total",
        "footer": "Show this QR code when boarding",
    },
}


def _route_label(route, language: str) -> str:
    if route.name:
        return route.name
    if language == "en":
        return f"{route.origin_city.name_en} → {route.destination_city.name_en}"
    return f"{route.origin_city.name_km} → {route.destination_city.name_km}"


def _qr_image_reader(payload: str) -> ImageReader:
    return ImageReader(io.BytesIO(qr_png_bytes(payload)))


def generate_ticket_pdf(booking, language: str = "km") -> bytes:
    """Renders a one-page A5 e-ticket PDF for `booking`. `booking.qr_token`
    must already be set (confirm_booking sets it) — that same signed token is
    what the boarding app's QR scanner (Step 10) verifies."""
    register_kantumruy_pro()
    labels = _LABELS[language if language in _LABELS else "km"]

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A5)
    width, height = A5
    margin = 30
    y = height - margin

    pdf.setFont(_BOLD_FONT, 18)
    pdf.drawString(margin, y, labels["title"])
    y -= 28

    pdf.setFont(_REGULAR_FONT, 12)
    trip = booking.trip
    departure_local = timezone.localtime(trip.departure_at).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"{labels['pnr']}: {booking.pnr}",
        f"{labels['route']}: {_route_label(trip.route, language)}",
        f"{labels['departure']}: {departure_local}",
        f"{labels['total']}: {booking.total_amount} {booking.currency}",
    ]
    for line in lines:
        pdf.drawString(margin, y, line)
        y -= 18

    y -= 6
    pdf.setFont(_BOLD_FONT, 12)
    pdf.drawString(margin, y, labels["passengers"])
    y -= 18
    pdf.setFont(_REGULAR_FONT, 11)
    for passenger in booking.passengers.select_related("trip_seat__seat").order_by("id"):
        seat_label = passenger.trip_seat.seat.seat_number
        pdf.drawString(margin, y, f"- {passenger.full_name} ({labels['seat']} {seat_label})")
        y -= 16

    qr_size = 140
    qr_reader = _qr_image_reader(booking.qr_token or booking.pnr)
    pdf.drawImage(qr_reader, width - margin - qr_size, margin + 20, width=qr_size, height=qr_size)

    pdf.setFont(_REGULAR_FONT, 9)
    pdf.drawString(margin, margin, labels["footer"])

    pdf.showPage()
    pdf.save()
    return buf.getvalue()
