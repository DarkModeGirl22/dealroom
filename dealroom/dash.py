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


def facts(conn, me) -> str:
    """Exact facts computed by code from the record, given to the AI assistant with every question,
    so it answers with real numbers (and forecasts from them) instead of guessing."""
    data = build(conn, me)
    deals, pays, alerts = data["deals"], data["payments"], data["alerts"]
    seller = me["role"] == "freelancer"
    who = "client" if seller else "freelancer"
    agreed = [d for d in deals if d["hasTerms"]]
    lines = [f"Today is {db.now()[:10]}. {me['name']} is a {me['role']}. Amounts are US dollars. "
             f"Names marked (AI) are people played by AI for the demo.",
             f"Totals: {len(deals)} deals ({len(agreed)} with agreed terms); "
             f"{'earned' if seller else 'paid'} through PayPal ${sum(p['amount'] for p in pays):,.2f} in {len(pays)} payments; "
             f"still {'owed to me' if seller else 'owed by me'} ${sum(d['owed'] for d in deals):,.2f}; "
             f"open issues {sum(d['openIssue'] for d in deals)}; scam warnings {sum(a['isScam'] for a in alerts)}."]
    people = {}
    for d in deals:
        people.setdefault(d["counterpart"], []).append(d)
    returning = [n for n, ds in people.items() if len([d for d in ds if d["hasTerms"]]) >= 2]
    with_deal = [n for n, ds in people.items() if any(d["hasTerms"] for d in ds)]
    if with_deal:
        lines.append(f"Return rate: {len(returning)} of {len(with_deal)} {who}s with an agreed deal came back for another "
                     f"({100 * len(returning) // len(with_deal)}%): {', '.join(returning) or 'none'}.")
    lines.append(f"\nPer {who} (deals, agreed, completed, paid, owed, disputes, files that didn't match, files downloaded, scam warnings, first and last activity):")
    for n, ds in sorted(people.items(), key=lambda kv: -sum(d["paid"] for d in kv[1])):
        sc = sum(a["isScam"] for a in alerts if a["counterpart"] == n)
        lines.append(f"- {n}: {len(ds)} deals, {sum(d['hasTerms'] for d in ds)} agreed, "
                     f"{sum(d['status'] == 'Completed' for d in ds)} completed, paid ${sum(d['paid'] for d in ds):,.2f}, "
                     f"owed ${sum(d['owed'] for d in ds):,.2f}, {sum(d['status'] == 'Dispute open' for d in ds)} disputes, "
                     f"{sum(d['fileCheck'] == 'Doesn' + chr(39) + 't match' for d in ds)} mismatched files, "
                     f"{sum(d['hasDownload'] for d in ds)} downloads, {sc} scam warnings, "
                     f"{min(d['started'] for d in ds)} to {max(d['lastActivity'] for d in ds)}")
    months = {}
    for p in pays:
        months[p["date"][:7]] = months.get(p["date"][:7], 0) + p["amount"]
    lines.append("\nPayments by month: " + "; ".join(f"{m} ${v:,.2f}" for m, v in sorted(months.items())))
    lines.append("\nEvery deal (title | with | status | price | paid | owed | file check | next action | started | last activity):")
    for d in deals:
        lines.append(f"- {d['deal']} | {d['counterpart']} | {d['status']} | "
                     f"{'$%.2f' % d['price'] if d['price'] else 'no price yet'} | ${d['paid']:.2f} | ${d['owed']:.2f} | "
                     f"{d['fileCheck']} | {d['nextAction']} | {d['started']} | {d['lastActivity']}")
    lines.append("\nWarnings and checks on record:")
    lines += [f"- {a['date']} {a['counterpart']}: {a['kind']}: {a['text'][:140]}" for a in alerts if a["kind"] != "Payment"][:25]
    return "\n".join(lines)
