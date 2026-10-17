"""Protection: scam signals in the chat, and the private honesty check before a claim is sent.

The AI only classifies what a message is about. The facts it is compared with always come from
the room's record (payments, delivery, downloads, terms), written by code, never by the model.
"""

from __future__ import annotations

import re

from . import billing as B, db, delivery as D, docs, llm, room as R, terms as T

# cheap signals, checked on every message before any AI
OFF_PLATFORM = re.compile(r"friends\s*(?:&|and)\s*family|\bf\s*&\s*f\b|\bzelle\b|cash\s*app|\bvenmo\b|\bcrypto|bitcoin|"
                          r"\busdt\b|western union|gift ?card|whatsapp|telegram|outside (?:of )?(?:the )?(?:app|room|paypal)|"
                          r"pay me directly|send (?:it|money) directly", re.I)
REFUND_ASK = re.compile(r"(?:sent|paid|transferred).{0,30}(?:too much|extra|by mistake|twice)|"
                        r"(?:send|give|transfer).{0,20}(?:back|the difference|the rest)", re.I)
LINK = re.compile(r"https?://\S+|www\.\S+", re.I)
CLAIMS = re.compile(r"receiv|never got|didn'?t get|not get|deliver|download|paid|pay|not what|wrong|didn'?t ask|"
                    r"late|missing|refund|scam|fraud|lie", re.I)

CLASSIFY = (
    "A message in a chat between a freelancer and a client. Say what it claims or asks, as JSON only: "
    "{\"claims\": list of zero or more of [\"not_received\", \"not_paid\", \"not_as_described\", \"late\", "
    "\"wants_refund\", \"off_platform_payment\", \"pressure\"]}. Only list what the message clearly says."
)


def scan(conn, room, sender, text: str):
    """After a message is sent: a shared, calm warning when it carries a known scam signal."""
    if OFF_PLATFORM.search(text):
        R.note(conn, room["id"], "warn", "Payments and messages outside this room aren't protected or recorded. "
               "Pay only with the PayPal button on the deal card; it's the only payment DealRoom can prove.")
    elif REFUND_ASK.search(text) and R.side(room, sender) == "client":
        R.note(conn, room["id"], "warn", "Never send money back by hand. If anything was overpaid, DealRoom refunds it "
               "only to the original PayPal payment, so it can't be reversed later.")
    elif LINK.search(text):
        R.note(conn, room["id"], "info", "A link was shared. DealRoom never asks you to log in or pay through a link; "
               "real invoices and payments only appear on the deal card.", only_for=R.other(conn, room, sender)["id"])


def facts(conn, room) -> dict:
    t = T.latest(conn, room["id"])
    dv = D.latest(conn, room["id"])
    return {"terms": t["data"] if t and t["agreed"] else None, "parts": B.parts(conn, room), "delivery": dv}


def _claims(text: str) -> set:
    if not CLAIMS.search(text):
        return set()
    out = llm.ask(CLASSIFY, text, task="fast", max_tokens=120) if llm.available() else None
    claims = set((out or {}).get("claims") or [])
    if not claims:
        if re.search(r"never (?:got|received)|didn'?t (?:get|receive)|not (?:received|delivered)", text, re.I):
            claims.add("not_received")
        if re.search(r"(?:didn'?t|never|haven'?t|not) (?:been )?pa(?:y|id)", text, re.I):
            claims.add("not_paid")
    return claims


def _contradictions(conn, room, person, claims) -> list[tuple[str, str]]:
    """Facts from the record that contradict the claims: (said to the author, said to both)."""
    f, side, me = facts(conn, room), R.side(room, person), person["name"]
    dv, out = f["delivery"], []
    if "not_received" in claims and side == "client" and dv:
        mine = [x for x in dv["downloads"] if x["name"] == me]
        if mine:
            when = docs.when(mine[0]["ts"])
            out.append((f"the record shows you downloaded \"{dv['filename']}\" on {when}",
                        f"\"{dv['filename']}\" was delivered on {docs.when(dv['ts'])} and downloaded by {me} on {when}"))
        else:
            out.append((f"\"{dv['filename']}\" was delivered on {docs.when(dv['ts'])} and is on the deal card",
                        f"\"{dv['filename']}\" was delivered on {docs.when(dv['ts'])}; it hasn't been downloaded yet"))
    if "not_paid" in claims and side == "freelancer":
        paid = [p for p in f["parts"] if p["paid"]]
        if paid:
            t = ", ".join(f"the {p['part']} (${p['amount']:,.2f}) on {docs.when(p['paid_ts'])}" for p in paid)
            out.append((f"PayPal confirmed {t}", f"PayPal confirmed {t}"))
    if "not_as_described" in claims and side == "client" and dv and dv["check"].get("checks"):
        ok = [c["requirement"] for c in dv["check"]["checks"] if c["result"] == "yes"]
        if ok:
            out.append(("the file check found these terms met: " + ", ".join(ok),
                        "the file check found these terms met: " + ", ".join(ok)))
    return out


def precheck(conn, room, person, text: str) -> str | None:
    """Before a message is sent: if it claims something the record contradicts, tell its author, privately."""
    lines = _contradictions(conn, room, person, _claims(text))
    if not lines:
        return None
    msg = "Before you send: " + "; ".join(a for a, _ in lines) + ". If you mean something else, for example that " \
          "something doesn't match what you asked, say exactly what, and it will be easier to fix."
    return msg   # shown once, in the private panel above the message box, never to the other person


def after_claim(conn, room, person, text: str):
    """A complaint was sent anyway: show both people the same facts, calmly, before it becomes a dispute."""
    lines = _contradictions(conn, room, person, _claims(text))
    if lines:
        R.note(conn, room["id"], "info", "Facts from the record, for both of you: " + "; ".join(b for _, b in lines) +
               ". If something is still wrong, say exactly what, so it can be fixed here.")
