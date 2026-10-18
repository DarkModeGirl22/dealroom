"""Invoice and payments: amounts come only from the accepted terms."""

from __future__ import annotations

from . import db, docs, paypal, room as R, terms as T


def invoice_number(room_id: int, version: int) -> str:
    return f"DR-{room_id:04d}-{version}"


def make_invoice(conn, room) -> str | None:
    t = T.latest(conn, room["id"])
    if not t or not t["agreed"]:
        return None
    if t["invoice_number"]:
        return t["invoice_number"]
    num = invoice_number(room["id"], t["version"])
    fl, cl = R.person_by_id(conn, room["freelancer_id"]), R.person_by_id(conn, room["client_id"])
    pid = None
    if paypal.configured():
        try:
            pid = paypal.create_invoice(t["data"], fl["email"], cl["email"], num + "-" + db.now()[11:19].replace(":", ""))["id"]
        except paypal.PayPalError as e:
            R.note(conn, room["id"], "warn", f"PayPal didn't accept the invoice ({str(e)[:120]}). The PDF copy is still made.")
    db.update(conn, "terms", t["id"], invoice_number=num, invoice_id=pid)
    msg = f"Invoice {num} made from the accepted terms"
    msg += f" and created on PayPal ({pid})." if pid else "."
    R.note(conn, room["id"], "good", msg + " Download the PDF from the deal card.")
    return num


def invoice_pdf(conn, room) -> bytes | None:
    t = T.latest(conn, room["id"])
    if not t or not t["invoice_number"]:
        return None
    return docs.invoice_pdf(room, t, R.person_by_id(conn, room["freelancer_id"]), R.person_by_id(conn, room["client_id"]),
                            t["invoice_number"], t["invoice_id"])


def parts(conn, room) -> list[dict]:
    """What the client has to pay, part by part, from the terms, with what's already paid."""
    t = T.latest(conn, room["id"])
    if not t or not t["agreed"]:
        return []
    d = t["data"]
    dep = d.get("deposit")
    plan = [("deposit", dep), ("final", round(d["price"] - dep, 2))] if dep and dep < d["price"] else [("full", d["price"])]
    out = []
    for part, amount in plan:
        p = db.one(conn, "SELECT * FROM payment WHERE room_id=? AND part=? ORDER BY id DESC LIMIT 1", room["id"], part)
        out.append({"part": part, "amount": amount, "paid": bool(p and p["status"] == "COMPLETED"),
                    "paid_ts": p["paid_ts"] if p else None, "capture_id": p["capture_id"] if p else None})
    return out


def create_order(conn, room, part: str, custom_id: str | None = None) -> str:
    """Create the PayPal order for one part. The amount comes from the terms, never from the browser."""
    plan = {p["part"]: p for p in parts(conn, room)}
    if part not in plan or plan[part]["paid"]:
        raise ValueError("nothing to pay for this part")
    t = T.latest(conn, room["id"])
    amount = plan[part]["amount"]
    label = {"deposit": "deposit", "final": "final payment", "full": "payment"}[part]
    # PayPal refuses to capture a second order with an invoice id it has seen, and every private copy (and every
    # "Start over") numbers its invoices the same way: so each order gets its own id, built from the invoice number
    import uuid
    order = paypal.create_order(amount, f"{t['data']['item']} ({label}, invoice {t['invoice_number']})",
                                f"room-{room['id']}-{part}", f"{t['invoice_number']}-{part}-{uuid.uuid4().hex[:10]}",
                                custom_id)
    db.insert(conn, "payment", room_id=room["id"], part=part, amount=amount, order_id=order["id"], status="CREATED",
              ts=db.now())
    return order["id"]


def capture(conn, room, order_id: str, custom: str | None = None) -> dict:
    p = db.one(conn, "SELECT * FROM payment WHERE room_id=? AND order_id=?", room["id"], order_id)
    if not p:
        raise ValueError("unknown order")
    if p["status"] == "COMPLETED":
        return dict(p)
    res = paypal.capture_order(order_id)
    cap = res["purchase_units"][0]["payments"]["captures"][0]
    paid = float(cap["amount"]["value"])
    payer = (res.get("payer") or {}).get("email_address")
    db.update(conn, "payment", p["id"], status=cap["status"], capture_id=cap["id"], payer_email=payer, paid_ts=db.now())
    remember_capture(cap["id"], custom)
    label = {"deposit": "Deposit", "final": "Final payment", "full": "Payment"}[p["part"]]
    if abs(paid - p["amount"]) < 0.005 and cap["status"] == "COMPLETED":
        R.note(conn, room["id"], "good", f"{label} of ${paid:,.2f} received through PayPal. It matches the terms exactly.",
               {"capture_id": cap["id"]})
    else:
        R.note(conn, room["id"], "bad", f"{label}: PayPal reports ${paid:,.2f} ({cap['status']}), the terms say "
                                        f"${p['amount']:,.2f}. Don't send money back by hand; DealRoom refunds only to the original payment.")
    if all(x["paid"] for x in parts(conn, room)):
        R.note(conn, room["id"], "good", "Everything in the terms is paid.")
    return db.one(conn, "SELECT * FROM payment WHERE id=?", p["id"])


# webhooks arrive without a visitor cookie: remember which private copy each PayPal capture belongs to
def _index():
    return db.DATA_DIR / "captures.json"


def remember_capture(capture_id: str, custom: str | None):
    import json
    if not custom:
        return
    try:
        idx = json.loads(_index().read_text())
    except (OSError, ValueError):
        idx = {}
    idx[capture_id] = custom
    _index().write_text(json.dumps(idx))


def lookup_capture(capture_id: str) -> str | None:
    import json
    try:
        return json.loads(_index().read_text()).get(capture_id)
    except (OSError, ValueError):
        return None

