"""The data behind "My business" (freelancer) and "My purchases" (client): every deal, payment and alert
of the signed-in person, read from the record, plus the one next action each deal needs."""

from __future__ import annotations

from . import billing as B, db, delivery as D, disputes as X, room as R, terms as T

SCAM = ("outside this room", "Never send money back", "A link was shared")


def _status(t, parts, dv, x):
    if x and x["status"] != "resolved":
        return "Dispute open"
    if dv and dv["check"].get("checks") and any(c["result"] == "no" for c in dv["check"]["checks"]):
        return "Doesn't match the terms"
    paid = [p for p in parts if p["paid"]]
    if not t:
        return "Talking"
    if not t["agreed"]:
        return "Terms to accept"
    if dv and parts and len(paid) == len(parts):
        return "Completed"
    if dv:
        return "Delivered, payment due"
    if paid:
        return "Paid, in progress"
    return "Awaiting payment"


def _next(me, other, t, parts, dv, x):
    """The one thing this deal needs from me now, or None."""
    freelancer = me["role"] == "freelancer"
    due = [p for p in parts if not p["paid"]]
    if x and x["status"] != "resolved":
        if freelancer and not x.get("report"):
            return ("Prepare your evidence for PayPal", "bad")
        if freelancer and not x.get("sent_ts"):
            return ("Send your evidence to PayPal", "bad")
        return ("PayPal is reviewing the dispute", "warn")
    if dv and dv["check"].get("checks") and any(c["result"] == "no" for c in dv["check"]["checks"]):
        return ("Ask for a fix, or report it to PayPal", "bad") if not freelancer else ("Deliver a fixed version", "bad")
    if not t:
        return (f"Agree on a price with {other}", "info")
    if not t["agreed"]:
        mine = t["accepted_freelancer" if freelancer else "accepted_client"]
        return ((f"Waiting for {other} to accept" , "info") if mine else ("Accept the terms", "warn"))
    if not t["invoice_number"]:
        return ("Make the PayPal invoice", "warn") if freelancer else (f"Waiting for {other}'s invoice", "info")
    if due and (dv or not any(p["paid"] for p in parts)):
        amount = sum(p["amount"] for p in due) if dv else due[0]["amount"]
        return (f"{other} owes you ${amount:,.2f}", "warn") if freelancer else (f"Pay ${amount:,.2f} with PayPal", "warn")
    if not dv:
        return ("Deliver the file", "info") if freelancer else (f"Waiting for {other}'s file", "info")
    return None


def build(conn, me) -> dict:
    deals, payments, alerts = [], [], []
    for rm in R.rooms_for(conn, me):
        rm = R.get(conn, rm["id"])
        other = R.other(conn, rm, me)
        t = T.latest(conn, rm["id"])
        d = t["data"] if t else {}
        parts = B.parts(conn, rm) if t and t["agreed"] else []
        dv = D.latest(conn, rm["id"])
        x = X.latest(conn, rm["id"])
        paid = round(sum(p["amount"] for p in parts if p["paid"]), 2)
        price = d.get("price")
        last = db.one(conn, "SELECT MAX(ts) m FROM message WHERE room_id=?", rm["id"])["m"] or rm["created"]
        nxt = _next(me, other["name"], t, parts, dv, dict(x) if x else None)
        checks = (dv or {}).get("check", {}).get("checks") or []
        deals.append({
            "id": f"d{rm['id']}", "roomId": rm["id"], "deal": rm["title"], "counterpart": other["name"],
            "status": _status(t, parts, dv, x), "price": price, "paid": paid,
            "owed": round((price or 0) - paid, 2) if t and t["agreed"] else 0,
            "started": (rm["created"] or "")[:10], "lastActivity": last[:10],
            "hasTerms": bool(t and t["agreed"]), "hasPayment": paid > 0, "hasDelivery": dv is not None,
            "hasDownload": bool(dv and dv["downloads"]),
            "fileCheck": "Not delivered" if not dv else "Matches the terms" if checks and all(c["result"] == "yes" for c in checks)
                         else "Doesn't match" if any(c["result"] == "no" for c in checks) else "Not checked",
            "nextAction": nxt[0] if nxt else "Nothing to do", "nextLevel": nxt[1] if nxt else "good",
            "aiPlayed": other["key"] in ("sam", "rafael"),
            "openIssue": 1 if _status(t, parts, dv, x) in ("Dispute open", "Doesn't match the terms") else 0,
            "needsYou": 1 if nxt and nxt[1] in ("warn", "bad") else 0,
            "deals": 1,
        })
        for p in db.all_(conn, "SELECT * FROM payment WHERE room_id=? AND status='COMPLETED'", rm["id"]):
            payments.append({"id": f"p{p['id']}", "dealId": f"d{rm['id']}", "counterpart": other["name"],
                             "part": p["part"].capitalize(), "amount": p["amount"], "date": (p["paid_ts"] or p["ts"])[:10],
                             "source": "PayPal sample" if (p["order_id"] or "").startswith("SAMPLE") else "PayPal sandbox"})
        for n in db.all_(conn, """SELECT * FROM message WHERE room_id=? AND kind IN ('note','private')
                                  AND (only_for IS NULL OR only_for=?)""", rm["id"], me["id"]):
            text = n["text"]
            kind = ("Scam warning" if any(s in text for s in SCAM) else
                    "Dispute" if "dispute" in text.lower() else
                    "Honesty check" if text.startswith("Facts from the record") else
                    "File check" if "checked the file" in text else
                    "Payment" if "received through PayPal" in text else None)
            if kind:
                alerts.append({"id": f"a{n['id']}", "dealId": f"d{rm['id']}", "counterpart": other["name"], "kind": kind,
                               "level": n["level"] or "info", "date": n["ts"][:10], "text": text[:200],
                               "isScam": 1 if kind == "Scam warning" else 0})
    # a fixed order of statuses, so each one keeps its colour in the charts (green done ... coral dispute)
    order = ["Completed", "Delivered, payment due", "Paid, in progress", "Awaiting payment", "Terms to accept", "Talking",
             "Doesn't match the terms", "Dispute open"]
    deals.sort(key=lambda d: order.index(d["status"]) if d["status"] in order else 99)
    return {"me": {"key": me["key"], "name": me["name"], "role": me["role"]},
            "deals": deals, "payments": payments, "alerts": alerts}
