"""PDFs from the record: the invoice copy (and later the evidence report)."""

from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

STY = getSampleStyleSheet()


def when(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).strftime("%b %d, %Y %H:%M UTC")
    except ValueError:
        return ts


def _doc(story) -> bytes:
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title="DealRoom").build(story)
    return buf.getvalue()


def _table(rows, widths):
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9.5), ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return t


def invoice_pdf(room, terms: dict, freelancer, client, number: str, paypal_id: str | None) -> bytes:
    d = terms["data"]
    s = [Paragraph(f"Invoice {number}", STY["Title"]),
         Paragraph(f"{d.get('item')} &middot; agreed in DealRoom, room #{room['id']}", STY["Normal"]), Spacer(1, 8)]
    rows = [["From", f"{freelancer['name']} ({freelancer['email']})"], ["To", f"{client['name']} ({client['email']})"],
            ["Work", d.get("item") or ""], ["Deliverables", "; ".join(d.get("deliverables") or []) or "-"],
            ["Total", f"${d['price']:,.2f} USD"]]
    if d.get("deposit"):
        rows += [["Deposit before work", f"${d['deposit']:,.2f}"], ["Due at delivery", f"${d['price'] - d['deposit']:,.2f}"]]
    if d.get("revisions") is not None:
        rows.append(["Revisions included", str(d["revisions"])])
    if d.get("deadline"):
        rows.append(["Delivery", d["deadline"]])
    if d.get("refund_policy"):
        rows.append(["Refunds", d["refund_policy"]])
    rows += [["Terms version", f"{terms['version']} ({terms['source']})"],
             ["Accepted by freelancer", when(terms["accepted_freelancer"])], ["Accepted by client", when(terms["accepted_client"])],
             ["PayPal invoice", paypal_id or "not created (PayPal keys not set)"]]
    s += [_table([[a, Paragraph(str(b), STY["Normal"])] for a, b in rows], [48 * mm, 122 * mm]), Spacer(1, 10),
          Paragraph("Payments are made only through the PayPal button in the room, for the amounts above. "
                    "Tips are off; any refund goes back to the original payment.", STY["Italic"])]
    return _doc(s)
