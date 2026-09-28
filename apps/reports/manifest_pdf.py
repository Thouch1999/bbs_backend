"""
Operator-facing manifest export (FR3.2), as a simple table — Khmer names
render via apps.core.pdf_fonts, the same Kantumruy Pro setup as the e-ticket.
"""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from apps.core.pdf_fonts import BOLD_FONT, REGULAR_FONT, register_kantumruy_pro


def generate_manifest_pdf(trip, manifest: list[dict]) -> bytes:
    register_kantumruy_pro()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4))

    header = ["PNR", "Passenger", "Seat", "Boarded"]
    rows = [header] + [
        [row["pnr"], row["full_name"], row["seat_number"], "Yes" if row["boarded"] else "No"]
        for row in manifest
    ]
    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("FONTNAME", (0, 1), (-1, -1), REGULAR_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
            ]
        )
    )
    doc.build([table])
    return buf.getvalue()
