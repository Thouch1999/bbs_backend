"""
Shared PDF font setup for the e-ticket (notifications) and manifest exports
(reports) — both need Khmer text to render correctly. Kantumruy Pro (the
locked-decision font, see CLAUDE.md) is self-hosted here as two static
instances generated from Google Fonts' variable KantumruyPro[wght].ttf
(reportlab can't render a variable font's glyph outlines correctly) — see
apps/core/fonts/OFL.txt for licensing.
"""

from django.conf import settings
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = settings.BASE_DIR / "apps" / "core" / "fonts"
REGULAR_FONT = "KantumruyPro"
BOLD_FONT = "KantumruyPro-Bold"
_registered = False


def register_kantumruy_pro() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(REGULAR_FONT, str(FONT_DIR / "KantumruyPro-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(BOLD_FONT, str(FONT_DIR / "KantumruyPro-Bold.ttf")))
    _registered = True
