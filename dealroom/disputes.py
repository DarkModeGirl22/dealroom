"""Disputes: the room's record becomes a three-part report for PayPal.

1. a neutral timeline, written by code from the record (every line has a time and a source);
2. each side's statement, written by NVIDIA Nemotron from the SAME timeline;
3. a short neutral assessment.
Every sentence the AI writes must cite timeline events [E1], [E2]...; sentences without a valid
citation, or with a number that isn't in the events they cite, are removed. So the AI can't help
anyone lie, including the person who asked for the report.
"""

from __future__ import annotations

import io
import json
import re

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from . import billing as B, db, delivery as D, docs, llm, paypal, room as R, terms as T

REASONS = {"not_received": "Item not received", "not_as_described": "Significantly not as described",
           "unauthorized": "Unauthorized payment"}

WRITE = (
    "You help write a PayPal dispute file. You get a numbered timeline of facts recorded in a deal room and the "
    "client's claim. Write only what the timeline supports, calmly and briefly. Each sentence comes with the ids "
    "of the timeline events it relies on. Never add a fact, number or date that is not in those events.\n"
    "Reply JSON only: {\"freelancer\": [{\"s\": sentence, \"e\": [\"E3\", ...]}, ...] (2 to 4 sentences, the "
    "freelancer's side), \"client\": [same shape] (2 to 4 sentences, the client's side as fairly as the facts "
    "allow), \"assessment\": [same shape] (2 to 3 neutral sentences on what the record shows about the claim), "
    "\"supports\": \"freelancer\" or \"client\" or \"unclear\"}"
)


def timeline(conn, room) -> list[dict]:
    """The neutral record, in order. Private notes are never included."""
    ev = []
    chat = db.all_(conn, """SELECT m.id, m.text, m.ts, p.name FROM message m JOIN person p ON p.id=m.sender_id
                            WHERE m.room_id=? AND m.kind='chat' ORDER BY m.id""", room["id"])
    for m in chat:
        ev.append({"ts": m["ts"], "what": f"{m['name']} wrote: \"{m['text'][:200]}\"", "src": f"message #{m['id']}"})
    for t in db.all_(conn, "SELECT * FROM terms WHERE room_id=? ORDER BY version", room["id"]):
        d = db.jload(t["data"], {})
        if t["accepted_freelancer"] and t["accepted_client"]:
            later = max(t["accepted_freelancer"], t["accepted_client"])
            bits = [d.get("item") or "work", f"total ${d['price']:,.2f}" if d.get("price") is not None else None,
                    f"deposit ${d['deposit']:,.2f}" if d.get("deposit") else None,
                    f"{d['revisions']} revisions" if d.get("revisions") is not None else None,
                    f"delivery {d['deadline']}" if d.get("deadline") else None]
            ev.append({"ts": later, "what": "Both accepted terms version %d: %s" % (t["version"], ", ".join(b for b in bits if b)),
                       "src": f"terms v{t['version']}"})
        if t["invoice_number"]:
            ev.append({"ts": later, "what": f"Invoice {t['invoice_number']} issued from those terms"
                       + (f" (PayPal {t['invoice_id']})" if t["invoice_id"] else ""), "src": "invoice"})
    for p in db.all_(conn, "SELECT * FROM payment WHERE room_id=? AND status='COMPLETED' ORDER BY id", room["id"]):
        ev.append({"ts": p["paid_ts"], "what": f"PayPal confirmed the {p['part']} payment of ${p['amount']:,.2f} "
                   f"(capture {p['capture_id']})", "src": "PayPal"})
    for d in db.all_(conn, "SELECT * FROM delivery WHERE room_id=? ORDER BY id", room["id"]):
        ev.append({"ts": d["ts"], "what": f"File \"{d['filename']}\" delivered (fingerprint {d['sha256'][:12]})", "src": "delivery"})
        chk = db.jload(d["check_data"], {})
        if chk.get("checks"):
            res = "; ".join(f"{c['requirement']}: {c['result']}" for c in chk["checks"])
            ev.append({"ts": d["ts"], "what": f"AI file check against the terms: {res}", "src": "file check"})
        for dl in db.all_(conn, """SELECT dl.ts, p.name FROM download dl JOIN person p ON p.id=dl.person_id
                                   WHERE dl.delivery_id=? ORDER BY dl.id""", d["id"]):
            ev.append({"ts": dl["ts"], "what": f"{dl['name']} downloaded \"{d['filename']}\"", "src": "download log"})
    for x in db.all_(conn, "SELECT * FROM dispute WHERE room_id=? ORDER BY id", room["id"]):
        ev.append({"ts": x["ts"], "what": f"Dispute opened: {REASONS.get(x['reason'], x['reason'])}. Claim: \"{x['claim']}\"",
                   "src": "PayPal dispute" + (f" {x['paypal_id']}" if x["paypal_id"] else "")})
    ev.sort(key=lambda e: e["ts"] or "")
    for i, e in enumerate(ev, 1):
        e["id"] = f"E{i}"
    return ev


def open_dispute(conn, room, person, reason: str, claim: str, paypal_id: str | None = None) -> int:
    pays = [p for p in B.parts(conn, room) if p["paid"]]
    if R.side(room, person) != "client" or not pays:
        return 0
    did = db.insert(conn, "dispute", room_id=room["id"], paypal_id=paypal_id, reason=reason if reason in REASONS else "not_received",
                    claim=claim[:500] or REASONS.get(reason, ""), status="open", ts=db.now())
    R.note(conn, room["id"], "bad", f"A PayPal dispute was opened on this deal: {REASONS.get(reason, reason)}. "
                                    f"{room['freelancer']}, your evidence can be prepared from the room's record in one click.")
    db.update(conn, "room", room["id"], status="dispute")
    return did


def _grounded(items, events: dict) -> str:
    """Keep only sentences that cite real events and whose numbers all appear in those events."""
    keep = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        sent, refs = str(it.get("s") or "").strip(), [r for r in (it.get("e") or []) if isinstance(r, str)]
        if not sent or not refs or any(r not in events for r in refs):
            continue
        cited = " ".join(events[r]["what"] + " " + (events[r]["ts"] or "") for r in refs).replace(",", "")
        names = [w for w in re.findall(r"(?<!^)(?<![.!?] )\b[A-Z][A-Za-z]+", sent) if w not in ("PayPal", "AI", "I")]
        low = cited.lower()
        if all(n.replace(",", "") in cited for n in re.findall(r"\d+(?:[.,]\d+)?", sent)) \
                and all(w.lower() in low for w in names):
            keep.append(sent + " " + "".join(f"[{r}]" for r in refs))
    return " ".join(keep)


def prepare(conn, room, person) -> dict | None:
    x = db.one(conn, "SELECT * FROM dispute WHERE room_id=? ORDER BY id DESC LIMIT 1", room["id"])
    side = R.side(room, person)
    if not x or side not in ("freelancer", "client"):
        return None
    ev = timeline(conn, room)
    events = {e["id"]: e for e in ev}
    lines = "\n".join(f"[{e['id']}] {e['ts']} · {e['what']} (source: {e['src']})" for e in ev)
    raw = llm.ask(WRITE, f"Timeline:\n{lines}\n\nClient's claim: {REASONS.get(x['reason'])}: \"{x['claim']}\"",
                  task="smart", max_tokens=900) or {}
    rep = {k: _grounded(raw.get(k), events) for k in ("freelancer", "client", "assessment")}
    rep["supports"] = raw.get("supports") if raw.get("supports") in ("freelancer", "client", "unclear") else "unclear"
    rep["events"] = ev
    db.update(conn, "dispute", x["id"], report=json.dumps(rep))
    if side == "client":   # PayPal's API takes evidence from the seller; the buyer adds it to the case on PayPal
        R.note(conn, room["id"], "warn" if rep["supports"] == "freelancer" else "info",
               ("Your evidence report is ready. Honestly: the record supports the freelancer on this claim. "
                if rep["supports"] == "freelancer" else "Your evidence report is ready: the neutral timeline, both sides' "
                "statements and the assessment, each sentence tied to the record. ") +
               "Download the PDF and attach it to your case in PayPal's Resolution Center.", only_for=person["id"])
    elif rep["supports"] == "client":
        R.note(conn, room["id"], "warn", "Your evidence is ready, but honestly: the record doesn't support your side on "
               "this claim. Offering a refund through PayPal may be wiser than sending it.", only_for=person["id"])
    else:
        R.note(conn, room["id"], "info", "Your evidence report is ready: the neutral timeline, both sides' statements and "
               "the assessment, each sentence tied to the record. Check it, then send it to PayPal.", only_for=person["id"])
    return rep


def report_pdf(conn, room) -> bytes | None:
    x = db.one(conn, "SELECT * FROM dispute WHERE room_id=? ORDER BY id DESC LIMIT 1", room["id"])
    rep = db.jload(x["report"]) if x else None
    if not rep:
        return None
    S = docs.STY
    s = [Paragraph(f"Dispute evidence · {room['title']}", S["Title"]),
         Paragraph(f"DealRoom room #{room['id']} · {REASONS.get(x['reason'])} · claim: \"{x['claim']}\"", S["Normal"]),
         Spacer(1, 8), Paragraph("1. What the record shows (neutral timeline)", S["Heading2"]),
         docs._table([["", "When", "What happened", "Source"]] +
                     [[e["id"], docs.when(e["ts"]), Paragraph(e["what"], S["Normal"]), e["src"]] for e in rep["events"]],
                     [10 * mm, 36 * mm, 92 * mm, 32 * mm])]
    for title, k in (("2. The freelancer's side", "freelancer"), ("3. The client's side", "client"),
                     ("4. Neutral assessment", "assessment")):
        s += [Paragraph(title, S["Heading2"]), Paragraph(rep.get(k) or "No statement could be grounded in the record.", S["Normal"])]
    s += [Spacer(1, 8), Paragraph("Statements 2 to 4 were written by NVIDIA Nemotron from the timeline above. Every "
                                  "sentence cites the events it relies on; sentences that didn't were removed. DealRoom "
                                  "never decides disputes: PayPal does.", S["Italic"])]
    return docs._doc(s)


def send(conn, room, person) -> str | None:
    x = db.one(conn, "SELECT * FROM dispute WHERE room_id=? ORDER BY id DESC LIMIT 1", room["id"])
    if not x or not x["report"] or R.side(room, person) != "freelancer":
        return None
    rep = db.jload(x["report"])
    pdf = report_pdf(conn, room)
    note = (rep.get("assessment") or "")[:1800]
    if x["paypal_id"] and paypal.configured():
        try:
            paypal.provide_evidence(x["paypal_id"], pdf, note)
            how = f"sent to PayPal dispute {x['paypal_id']} through the Disputes API"
        except paypal.PayPalError as e:
            R.note(conn, room["id"], "warn", f"PayPal didn't accept the evidence: {str(e)[:150]}", only_for=person["id"])
            return None
    else:
        how = "ready for PayPal (demo dispute: in a real one it goes through PayPal's Disputes API)"
    db.update(conn, "dispute", x["id"], sent_ts=db.now(), status="evidence sent")
    R.note(conn, room["id"], "info", f"{person['name']}'s evidence report was {how}. PayPal decides; the full record is attached.")
    return how


def latest(conn, room_id):
    x = db.one(conn, "SELECT * FROM dispute WHERE room_id=? ORDER BY id DESC LIMIT 1", room_id)
    if not x:
        return None
    d = dict(x)
    d["report"] = db.jload(x["report"])
    d["reason_label"] = REASONS.get(x["reason"], x["reason"])
    return d


PAYPAL_REASONS = {"MERCHANDISE_OR_SERVICE_NOT_RECEIVED": "not_received",
                  "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": "not_as_described", "UNAUTHORISED": "unauthorized"}


def from_paypal(conn, room, kind: str, res: dict, capture_id: str | None):
    """A verified PayPal webhook, written into the room's record."""
    if kind == "PAYMENT.CAPTURE.REFUNDED":
        R.note(conn, room["id"], "info", "PayPal reports a refund on this deal, back to the original payment.")
    elif kind == "PAYMENT.CAPTURE.REVERSED":
        R.note(conn, room["id"], "bad", "PayPal reversed a payment on this deal (the payer's bank or PayPal took it back).")
    elif kind == "CUSTOMER.DISPUTE.CREATED":
        if db.one(conn, "SELECT 1 FROM dispute WHERE paypal_id=?", res.get("dispute_id")):
            return
        reason = PAYPAL_REASONS.get(res.get("reason"), "not_received")
        claim = ((res.get("messages") or [{}])[0].get("content") or REASONS.get(reason, ""))[:500]
        db.insert(conn, "dispute", room_id=room["id"], paypal_id=res.get("dispute_id"), reason=reason, claim=claim,
                  status="open", ts=db.now())
        db.update(conn, "room", room["id"], status="dispute")
        R.note(conn, room["id"], "bad", f"PayPal opened dispute {res.get('dispute_id')}: {REASONS.get(reason, reason)}. "
                                        f"{room['freelancer']}, your evidence can be prepared from the room's record in one click.")
    elif kind == "CUSTOMER.DISPUTE.RESOLVED":
        outcome = ((res.get("dispute_outcome") or {}).get("outcome_code") or "resolved").replace("_", " ").lower()
        conn.execute("UPDATE dispute SET status=? WHERE paypal_id=?", (outcome, res.get("dispute_id")))
        conn.commit()
        R.note(conn, room["id"], "info", f"PayPal closed the dispute: {outcome}.")
