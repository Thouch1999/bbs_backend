import io
import secrets
from decimal import ROUND_HALF_UP, Decimal

import qrcode
from django.conf import settings
from django.core import signing

# Unambiguous uppercase charset: no O/0 or I/1, so a PNR read aloud or
# handwritten at a bus counter can't be misheard/mistyped into a collision.
_PNR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PNR_LENGTH = 8
_QR_SALT = "apps.core.utils.qr_token"


def _random_pnr():
    return "".join(secrets.choice(_PNR_ALPHABET) for _ in range(_PNR_LENGTH))


def generate_pnr(exists_fn):
    """
    Generates an 8-char unambiguous uppercase PNR. `exists_fn(code) -> bool`
    checks the caller's storage for a collision; retries on the rare hit.
    """
    for _ in range(10):
        code = _random_pnr()
        if not exists_fn(code):
            return code
    raise RuntimeError("Could not generate a unique PNR after 10 attempts.")


def sign_qr_token(payload: dict) -> str:
    """Signs a booking QR payload (e.g. {"pnr": ..., "booking_id": ...})."""
    return signing.dumps(payload, salt=_QR_SALT)


def verify_qr_token(token: str, max_age: int | None = None) -> dict:
    """
    Verifies and decodes a QR token. Raises signing.BadSignature (tampered)
    or signing.SignatureExpired (expired) — callers turn these into the
    uniform {"error": {...}} response via apps.core.exceptions.
    """
    max_age = settings.QR_TOKEN_MAX_AGE_SECONDS if max_age is None else max_age
    return signing.loads(token, salt=_QR_SALT, max_age=max_age)


def qr_png_bytes(payload: str) -> bytes:
    """Renders `payload` as a PNG QR code. Shared by the e-ticket PDF
    (apps.notifications.eticket, wrapped in reportlab's ImageReader) and the
    web ticket page's inline <img> (apps.web.qr, wrapped as a data URI)."""
    qr = qrcode.QRCode(border=1, box_size=6)
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def to_khr(usd_amount: Decimal, rate: Decimal | None = None) -> Decimal:
    """Converts a USD DECIMAL amount to whole-KHR (KHR has no minor unit in practice)."""
    rate = rate if rate is not None else Decimal(str(settings.KHR_PER_USD))
    return (Decimal(usd_amount) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def to_usd(khr_amount: Decimal, rate: Decimal | None = None) -> Decimal:
    """Converts a KHR DECIMAL amount to USD, rounded to cents."""
    rate = rate if rate is not None else Decimal(str(settings.KHR_PER_USD))
    return (Decimal(khr_amount) / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
