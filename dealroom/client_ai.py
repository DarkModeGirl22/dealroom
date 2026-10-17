"""The AI-played client for the solo demo: Alex, played by NVIDIA Nemotron, clearly labelled.

He chats about his dog's portrait, tries one classic scam (paying as Friends & Family), accepts fair
terms, downloads the delivery and reacts to what he sees. He can't pay: PayPal needs a real person to
approve a payment, so the visitor switches to Alex for that one click.
"""

from __future__ import annotations

import json
import re
import threading

from . import billing as B, db, delivery as D, llm, room as R, terms as T

PERSONA = (
    "You play Alex, a friendly client chatting with Maya, a freelance illustrator, in a deal room. Alex wants a "
    "digital portrait of his dog Biscuit (a light brown dog with floppy ears) on a blue background. His budget is "
    "about $80 and he'd like to pay half before and half at delivery. He wants 2 revisions. He wants it by Friday. "
    "Alex is the client: he pays Maya, through the PayPal button on the deal card; Maya never pays Alex. "
    "The button appears after both accept the terms and Maya makes the invoice: never ask for a PayPal link, an "
    "email or any other way to pay. Maya makes the invoices, never Alex. "
    "Write short, natural chat messages (1 or 2 sentences), like texting. Never invent payments you didn't make. "
    "{extra}\nReply JSON only: {{\"message\": \"Alex's next message\"}}"
)
SCAM = ("In this message, casually ask if you can pay with PayPal Friends & Family instead, to avoid fees. "
        "Ask only this once.")


def is_ai_room(room) -> bool:
    return (room["story"] or "") == "ai"


def _state(conn, room) -> str:
    t = T.latest(conn, room["id"])
    bits = []
    if t:
        bits.append(f"Terms on the deal card (version {t['version']}): " + json.dumps(
            {k: t["data"].get(k) for k in ("item", "price", "deposit", "revisions", "deadline")}) +
            ("; both accepted" if t["agreed"] else "; not accepted by both yet"))
        if t["invoice_number"]:
            bits.append("Invoice made: the PayPal button is on the deal card.")
        elif t["agreed"]:
            bits.append("No invoice yet: Maya makes it from the deal card.")
    parts = B.parts(conn, room)
    paid = [p["part"] for p in parts if p["paid"]]
    due = [f"the {p['part']} (${p['amount']:.2f})" for p in parts if not p["paid"]]
    bits.append("Alex has paid: " + (", ".join(paid) if paid else "NOTHING yet. Never say you paid or sent money."))
    dv = D.latest(conn, room["id"])
    if dv:
        bits.append("Maya has ALREADY delivered the file (the work is done; no future delivery date applies any more). "
                    "The check against the terms says: " +
                    "; ".join(f"{c['requirement']} {c['result']}" for c in (dv["check"].get("checks") or [])))
    if due:
        bits.append("Still to pay: " + ", ".join(due) + (", due NOW because the file is delivered." if dv else "."))
    return "\n".join(bits) or "Nothing agreed yet."


def _reply(conn, room, extra: str = "", lock=None):
    alex = R.person_by_id(conn, room["client_id"])
    chat = R.thread_text(conn, room, limit=20)
    asked_scam = any("friends" in m["text"].lower() for m in chat if m["role"] == "client")
    mine = sum(1 for m in chat if m["role"] == "client")
    if not asked_scam and mine == 2 and not extra:
        extra = SCAM
    convo = "\n".join(f"{m['name']}: {m['text']}" for m in chat)
    out = llm.ask(PERSONA.format(extra=extra), f"Deal room state:\n{_state(conn, room)}\n\nChat so far:\n{convo}\n\n"
                  "Alex's next message:", task="fast", max_tokens=150)
    text = str((out or {}).get("message") or "").strip()
    # the AI client may not claim a payment that PayPal hasn't confirmed: the same rule as for people
    claims_paid = re.search(r"\b(?:i'?ve|i have|i just|just)\s+(?:sent|paid|transferred)|\bpayment (?:is )?sent\b", text, re.I)
    if claims_paid and not any(p["paid"] for p in B.parts(conn, room)):
        text = ""
    last = [m["text"] for m in chat if m["role"] == "client"][-1:] or [""]
    if text and text != last[0]:
        mid = R.say(conn, room["id"], alex["id"], text[:400])
        from . import protect as P, watch as WA
        P.scan(conn, room, alex, text)
        if WA.check(conn, R.get(conn, room["id"]), mid, alex) == "drafted" and lock is not None:
            react(conn, lock, room["id"], "terms")


def react(conn, lock, room_id: int, event: str):
    """Run Alex's turn in the background, so Maya's screen never waits for the AI."""
    first = R.get(conn, room_id)   # callers hold the lock; no thread at all for rooms without an AI client
    if not first or not is_ai_room(first) or not llm.available():
        return

    def run():
        with lock:
            room = R.get(conn, room_id)
            if not room or not is_ai_room(room) or not llm.available():
                return
            alex = R.person_by_id(conn, room["client_id"])
            if event == "message":
                _reply(conn, room, lock=lock)
            elif event == "terms":
                t = T.latest(conn, room_id)
                d = t["data"] if t else {}
                fair = d.get("price") is not None and d["price"] <= 90
                if fair:
                    T.accept(conn, room, alex)
                _reply(conn, room, "The terms just appeared on the deal card. " + (
                    "They look fair to Alex: say he accepted them." if fair else
                    "Alex hasn't accepted: ask about what is missing or too expensive."), lock=lock)
            elif event == "invoice":
                _reply(conn, room, "Maya just made the invoice. Say you'll pay the deposit now with the PayPal button.", lock=lock)
                R.note(conn, room_id, "info", "To pay as Alex, switch to Alex at the top: PayPal needs a real person "
                                              "to approve a payment, even in the sandbox.")
            elif event == "delivery":
                dv = D.latest(conn, room_id)
                if dv:
                    D.download(conn, room, alex, dv["id"], "ai-client")
                    bad = [c["requirement"] for c in (dv["check"].get("checks") or []) if c["result"] == "no"]
                    seen = dv["check"].get("seen") or ""
                    _reply(conn, room, ("Alex opened the file. What it shows: " + seen + " It doesn't match: " + ", ".join(bad) +
                                        ". Politely ask for a fix, using one of the revisions.") if bad else
                           ("Alex opened the file and loves it. Thank Maya and say you'll pay what's left now with the PayPal button. "
                            "Don't mention any delivery date: the file is already delivered.")
                           if any(not p["paid"] for p in B.parts(conn, room)) else
                           "Alex opened the file and loves it. Thank Maya; everything is already paid.", lock=lock)
    threading.Thread(target=run, daemon=True).start()
