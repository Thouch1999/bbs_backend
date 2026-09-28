import base64

from apps.core.utils import qr_png_bytes


def qr_data_uri(payload: str) -> str:
    """A `data:image/png;base64,...` string for an inline <img> — the ticket
    page's on-screen/print display, as opposed to the PDF download (which
    reuses apps.notifications.eticket.generate_ticket_pdf directly)."""
    encoded = base64.b64encode(qr_png_bytes(payload)).decode("ascii")
    return f"data:image/png;base64,{encoded}"
