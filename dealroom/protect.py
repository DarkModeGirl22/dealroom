"""Protection: scam signals in the chat, and the private honesty check before a claim is sent.

The AI only classifies what a message is about. The facts it is compared with always come from
the room's record (payments, delivery, downloads, terms), written by code, never by the model.
"""

from __future__ import annotations

import re

from . import billing as B, db, delivery as D, docs, llm, room as R, terms as T

# cheap signals, checked on every message before any AI
OFF_PLATFORM = re.compile(r"friends\s*(?:&|and|n)\s*family|\bf\s*(?:&|n|and)\s*f\b|\bzelle\b|cash\s*app|\bvenmo\b|\bcrypto|"
                          r"bitcoin|\bbtc\b|\beth\b|\busdt\b|binance|western union|moneygram|gift ?card|wise\b|revolut|"
                          r"\biban\b|bank transfer|wire transfer|virement|whatsapp|telegram|signal app|"
                          r"outside (?:of )?(?:the )?(?:app|room|paypal|site)|pay me directly|send (?:it|money|the money) directly|"
                          r"amis (?:et|&) famille|hors (?:de )?(?:l'app|paypal)|en dehors de", re.I)
REFUND_ASK = re.compile(r"(?:sent|paid|transferred).{0,30}(?:too much|extra|by mistake|twice)|"
                        r"(?:send|give|transfer|return).{0,20}(?:back|the difference)", re.I)
LINK = re.compile(r"https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|co|me|xyz|top|info|"
                  r"site|online|link|ly|app|page|shop|live)(?:/\S*)?\b", re.I)
# a cheap first filter, wide on purpose: the AI decides what the message really claims
CLAIMS = re.compile(r"receiv|never got|didn'?t get|not get|deliver|download|paid|pay|sent|not what|wrong|asked|ask for|"
                    r"isn'?t|aren'?t|doesn'?t|don'?t|wasn'?t|not (?:like|the|as)|late|missing|refund|money back|"
                    r"scam|fraud|lie|cheat|"
                    # French and Spanish, so a complaint in another language still reaches the AI
                    r"reçu|recu|livr|télécharg|telecharg|pay|envoy|rembours|pas (?:ce|comme|le|la|reçu)|jamais|arnaque|"
                    r"recib|entreg|pag|devol|nunca|estafa", re.I)

CLASSIFY = (
    "A message in a chat between a freelancer and a client, in any language. List what it claims or asks, as JSON "
    "only: {\"claims\": [...]}, using zero or more of these labels:\n"
    "not_received: the writer says they did NOT get the work (\"I never received it\").\n"
    "not_paid: the writer says they were NOT paid.\n"
    "says_paid: the writer says they already paid or sent the money (\"sent the deposit\"). A promise (\"I'll pay "
    "now\") is not says_paid.\n"
    "says_delivered: the writer says they already sent or delivered the work.\n"
    "not_as_described: the work doesn't match what was asked (wrong color, missing part, \"not what I asked\").\n"
    "late: the work is late.\n"
    "wants_refund: the writer wants their money back.\n"
    "off_platform_payment: asks to pay or be paid outside PayPal.\n"
    "pressure: threats or pressure to act fast.\n"
    "Saying they got the file, are downloading it, or like it is no claim at all: []."
)


def _said_lately(conn, room_id, text: str, only_for=None, window: int = 15) -> bool:
    """The same warning in the last few messages of this deal: once is a warning, ten times is noise."""
    rows = db.all_(conn, "SELECT text, only_for FROM message WHERE room_id=? ORDER BY id DESC LIMIT ?", room_id, window)
    return any(r["text"] == text and r["only_for"] == only_for for r in rows)


def _warn(conn, room, level, text, only_for=None):
    if not _said_lately(conn, room["id"], text, only_for):
        R.note(conn, room["id"], level, text, only_for=only_for)


OFF_WARNING = ("Payments and messages outside this room aren't protected or recorded. Pay only with the PayPal button "
               "on the deal card; it's the only payment DealRoom can prove.")
REFUND_WARNING = ("Never send money back by hand. If anything was overpaid, DealRoom refunds it only to the original "
                  "PayPal payment, so it can't be reversed later.")


def scan(conn, room, sender, text: str):
    """After a message is sent: a shared, calm warning when it carries a known scam signal (not repeated every time)."""
    if REFUND_ASK.search(text) or re.search(r"refund.{0,40}(?:wallet|address|wise|revolut|crypto|btc)", text, re.I):
        _warn(conn, room, "warn", REFUND_WARNING)
    elif OFF_PLATFORM.search(text):
        _warn(conn, room, "warn", OFF_WARNING)
    elif LINK.search(text):
        _warn(conn, room, "info", "A link was shared. DealRoom never asks you to log in or pay through a link; "
              "real invoices and payments only appear on the deal card.", only_for=R.other(conn, room, sender)["id"])


def facts(conn, room) -> dict:
    t = T.latest(conn, room["id"])
    dv = D.latest(conn, room["id"])
    return {"terms": t["data"] if t and t["agreed"] else None, "parts": B.parts(conn, room), "delivery": dv}


PROMISE = re.compile(r"\b(?:i'?ll|i will|i'?m going to|i am going to|gonna|going to|about to|will|let me|je vais|"
                     r"je te|voy a)\b", re.I)
DONE = re.compile(r"\b(?:already|yesterday|last (?:night|week)|this morning|just (?:sent|paid)|i'?ve|i have|déjà|hier|ya)\b", re.I)


def _claims(text: str) -> set:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    if not CLAIMS.search(text):
        return set()
    out = llm.ask(CLASSIFY, text, task="smart", max_tokens=120) if llm.available() else None
    claims = set((out or {}).get("claims") or [])
    if PROMISE.search(text) and not DONE.search(text):   # "I'll pay now" is a promise, not a claim about the past
        claims -= {"says_paid", "says_delivered"}
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
    if "says_paid" in claims and side == "client" and not any(p["paid"] for p in f["parts"]):
        out.append(("PayPal hasn't confirmed any payment in this deal yet; payments only count when they're made with "
                    "the PayPal button on the deal card",
                    "PayPal hasn't confirmed any payment in this deal yet"))
    if "says_delivered" in claims and side == "freelancer" and not dv:
        out.append(("no file has been delivered in this deal yet; deliver it on the deal card so it's recorded",
                    "no file has been delivered in this deal yet"))
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
    msg = "Before you send: " + "; ".join(a for a, _ in lines) + ". If you mean something else, say exactly what, " \
          "and it will be easier to sort out."
    return msg   # shown once, in the private panel above the message box, never to the other person


def after_claim(conn, room, person, text: str):
    """A complaint was sent anyway: show both people the same facts, calmly, before it becomes a dispute."""
    claims = _claims(text)
    lines = _contradictions(conn, room, person, claims)
    if not lines:
        return
    msg = ("Facts from the record, for both of you: " + "; ".join(b for _, b in lines) +
           ". If something is still wrong, say exactly what, so it can be fixed here.")
    said = db.one(conn, "SELECT 1 FROM message WHERE room_id=? AND kind='note' AND text=?", room["id"], msg)
    if said:   # the same facts were already shown: don't repeat them, point to the next step (once)
        if R.side(room, person) == "client":
            _warn(conn, room, "info", "These facts are already in the room. If you still disagree, use "
                  "\"Report a problem to PayPal\" on the deal card: PayPal decides, with the full record.",
                  only_for=person["id"])
        return
    R.note(conn, room["id"], "info", msg)
