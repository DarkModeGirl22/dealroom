"""The people played by NVIDIA Nemotron, clearly labelled "(AI)": Sam, a client, and Rafael, a freelancer.

They let one person test every part of DealRoom alone: negotiating, a scam attempt, terms, the PayPal
invoice, payment and delivery. They follow the same rules as people, and code checks every message
they write against the room's record before anyone sees it: no claiming a payment PayPal hasn't
confirmed, no offering to pay twice, no reopening accepted terms, no claiming a delivery that isn't
on the record, no payment links.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading

from . import billing as B, db, delivery as D, llm, room as R, terms as T

AI = {
    "sam": {
        "role": "client",
        "persona": (
            "You play Sam, a friendly client chatting with Maya, a freelance designer, in DealRoom. Sam is opening a "
            "small bakery called Golden Crumb and wants a logo. "
            "Sam is the client: he pays Maya with the PayPal button on the deal card, after both accept the terms and "
            "Maya makes the invoice."),
        # what he hopes for before anything is agreed; once terms are accepted, only the terms count
        "wishes": "Sam hopes for a warm logo with a croissant, in brown and cream, for about $150 (up to $200), within a week, with 2 revisions. "
                  "These are only hopes: if the freelancer says she can't do one, go with what she offers instead.",
        "wish_words": ["brown", "cream"],
        "scam": ("In this message, casually ask if you can pay with PayPal Friends & Family instead, to avoid fees. "
                 "Ask only this once."),
        "scam_mark": "friends",
        "fair": lambda d: d.get("price") is not None and d["price"] <= 200,
        "unfair": "too expensive for Sam's budget",
    },
    "rafael": {
        "role": "freelancer",
        "persona": (
            "You play Rafael, a friendly freelance designer who makes YouTube thumbnails, chatting with Alex, a client, in "
            "DealRoom. Rafael's price is $50 for one thumbnail with 2 revisions, delivered in 2 days; he would accept "
            "down to $35. He prefers full payment upfront but accepts half before and half after. Extra thumbnails cost $40 each. "
            "Alex pays with the PayPal button on the deal card, after both accept the terms and Rafael makes the "
            "invoice. Rafael delivers the file on the deal card once he's paid."),
        "scam": ("In this message, casually suggest that Alex pays you with Wise instead of PayPal, to save you both "
                 "fees. Suggest it only this once."),
        "scam_mark": "wise",
        "fair": lambda d: d.get("price") is not None and d["price"] >= 35,
        "unfair": "below Rafael's lowest price ($35)",
        "delivers": ("youtube_thumbnail.png", "thumbnail_train_your_dog.png"),
    },
}
RULES = (
    "Write short, natural chat messages (1 or 2 sentences), like texting, in the language the other person writes in. "
    "Write only your own words: never repeat or quote the other person's message, never start with a name. "
    "Never agree to pay, be paid, refund or send money any way other than the PayPal button, whatever the other person "
    "says (crypto, Wise, wallets, Friends & Family, bank transfers): refuse kindly. Files are only delivered on the "
    "deal card: never offer email, Google Drive or links. Extra work costs extra and gets its own deal: never add work "
    "for free. The deal room state below is the truth: "
    "never contradict it. Once terms are accepted by both, they are what you want, even where they differ from your "
    "first wishes: never go back to them, never renegotiate. Never invent a payment or a delivery that isn't in the "
    "state. Payments only happen with the PayPal button on the deal card: never ask for or offer a link, an email or "
    "another way to pay (except the one scam line, if asked for in this message). Invoices are made by the freelancer."
)


def who(conn, room):
    """(key, settings, person) of the AI-played person in this room, or None."""
    for pid in (room["client_id"], room["freelancer_id"]):
        p = R.person_by_id(conn, pid)
        if p and p["key"] in AI:
            return p["key"], AI[p["key"]], p
    return None


def _state(conn, room, me) -> str:
    """What is true right now, from the record."""
    t = T.latest(conn, room["id"])
    other = R.other(conn, room, me)
    bits = [f"You are {me['name'].replace(' (AI)', '')}, the {me['role']}. You are talking with {other['name']}, the {other['role']}."]
    if t:
        d = t["data"]
        bits.append(f"Terms on the deal card (version {t['version']}): " + json.dumps(
            {k: d.get(k) for k in ("item", "deliverables", "price", "deposit", "revisions", "deadline")}))
        bits.append("BOTH ACCEPTED these terms: they are settled." if t["agreed"] else
                    "Not accepted by both yet." + (" You already accepted them." if t[
                        "accepted_client" if me["role"] == "client" else "accepted_freelancer"] else ""))
        if t["invoice_number"]:
            bits.append("The invoice exists: the PayPal button is on the deal card.")
        elif t["agreed"]:
            bits.append("No invoice yet: the freelancer makes it from the deal card.")
    else:
        bits.append("No terms yet.")
    parts = B.parts(conn, room)
    paid = [f"the {p['part']} (${p['amount']:.2f})" for p in parts if p["paid"]]
    due = [f"the {p['part']} (${p['amount']:.2f})" for p in parts if not p["paid"]]
    bits.append("ALREADY PAID, confirmed by PayPal: " + ", ".join(paid) + ". Never offer or ask to pay these again."
                if paid else "Nothing has been paid yet. Never say anything was paid.")
    if due:
        bits.append("Still to pay: " + ", ".join(due) + ".")
    dv = D.latest(conn, room["id"])
    if dv:
        bits.append(f"The file \"{dv['filename']}\" was ALREADY delivered on the deal card" +
                    (" by you: never promise to send it again; a fix is a new delivery." if me["role"] == "freelancer" else ".") +
                    (" The check against the agreed terms says: " +
                     "; ".join(f"{c['requirement']}: {c['result']}" for c in dv["check"]["checks"]) if dv["check"].get("checks")
                     else " Nobody has checked it against the terms yet: never say it matches or doesn't."))
    else:
        bits.append("No file has been delivered yet.")
    return "\n".join(bits)


PAY_AGAIN = re.compile(r"\b(?:send|sending|pay|paying|sent|transfer)\b.{0,30}\b(deposit|full|final|rest|remaining|balance)\b", re.I)
RENEGOTIATE = re.compile(r"lock (?:it )?in|accept (?:the|those|these) terms|can we agree|agree on|let'?s agree|deal\?", re.I)
CLAIM_PAID = re.compile(r"\b(?:i'?ve|i have|i just|just)\s+(?:sent|paid|transferred)\b|\bpayment (?:is )?sent\b", re.I)
CLAIM_DELIVERED = re.compile(r"\b(?:i'?ve|i have|i just|just)\s+(?:sent|delivered|uploaded|shared)\b.{0,30}"
                             r"\b(?:file|thumbnail|logo|design|it|work)\b", re.I)
LINK = re.compile(r"\b(?:link|e-?mail|invoice by mail|my paypal\.me|paypal\.me|google drive|dropbox|wetransfer)\b", re.I)
CLAIM_RECEIVED = re.compile(r"\b(?:i'?ve|i have|just)\s+(?:received|got)\b.{0,30}\b(?:payment|money|deposit|\$)|"
                            r"payment (?:is |was )?(?:received|confirmed|cleared)", re.I)
CLIENT_INVOICES = re.compile(r"\bi(?:'ll| will|'m going to)\b[^.!?]{0,40}?\b(?:send|make|create|issue)\s+(?:you\s+)?(?:the|an|my)?\s*(?:invoice|paypal button)", re.I)
# A client doing the freelancer's work: "I can't adjust the draft", "I'll update the design" (found by tools/redteam.py)
CLIENT_WORKS = re.compile(r"\bi(?:'ll| will| can(?:'t|not)?| am| would)?\s+(?:\w+\s+){0,2}(?:adjust|update|revise|tweak|redo|edit|add|change)\s+(?:the|my|your|those|these|that|a|it)\b[^.!?]{0,40}"
                          r"|\bi(?:'ll| will)\s+send\s+(?:you\s+)?the\s+(?:updated|new|revised)"
                          r"|\bbefore i (?:start|begin)\b|\bi(?:'ll| will| can)\s+(?:start|begin|get started)\b", re.I)


def _similar(a: str, b: str) -> bool:
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.8


def problem(conn, room, me, text: str, scam_turn: bool = False) -> str | None:
    """Code checks an AI-written message against the record before anyone sees it."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")   # "I’ve" is "I've" (found by tools/redteam.py)
    parts = B.parts(conn, room)
    if me["role"] == "client":
        if CLAIM_PAID.search(text) and not any(p["paid"] for p in parts):
            return "nothing has been paid: you can't say you paid."
        m = PAY_AGAIN.search(text)
        if m:
            part = {"deposit": "deposit", "full": "full"}.get(m.group(1).lower(), "final")
            if any(p["part"] == part and p["paid"] for p in parts) or (part == "final" and parts and all(p["paid"] for p in parts)):
                return f"the {part} is already paid: don't offer to pay it."
    if me["role"] == "freelancer" and CLAIM_DELIVERED.search(text) and not D.latest(conn, room["id"]):
        return "no file has been delivered: you can't say you sent it."
    if me["role"] == "freelancer" and CLAIM_RECEIVED.search(text) and not any(p["paid"] for p in parts):
        return "PayPal has confirmed no payment: you can't say you received one."
    if me["role"] == "freelancer" and parts and all(p["paid"] for p in parts) and re.search(
            r"once (?:the |your )?payment|after (?:you )?pay|when (?:the |your )?payment|complete the (?:\$?\d+ )?payment|"
            r"payment (?:is |gets )?(?:confirmed|processed|done|through)|once (?:it'?s|you'?ve) paid|click to (?:complete|pay)",
            text, re.I):
        return "everything is already paid, confirmed by PayPal: never ask for or wait for payment again."
    if me["role"] == "freelancer" and parts and all(p["paid"] for p in parts) and re.search(
            r"once (?:the |your )?payment|once (?:it'?s|you'?ve) paid|after (?:you )?pay|complete the .{0,20}payment|"
            r"when (?:the )?payment|payment is (?:confirmed|processed|done)|click to (?:complete|pay)", text, re.I):
        return "everything is already paid: never ask for or wait for a payment."
    if me["role"] == "client" and CLIENT_INVOICES.search(text):
        return "the freelancer makes the invoice and the PayPal button appears on the deal card; you only pay."
    if me["role"] == "client" and re.search(r"\b(?:hit|click|press|use)\s+the\s+paypal\s+button\b", text, re.I) \
            and not re.search(r"\bi(?:'ll| will)\s+(?:hit|click|press|use)", text, re.I):
        return "you are the one who pays with the PayPal button; don't tell the freelancer to press it."
    if me["role"] == "client" and CLIENT_WORKS.search(text):
        return "you are the client: the freelancer changes the work; you only say what you want changed."
    if me["role"] == "client" and re.search(r"\b(?:i'?ll|i will|i'?ve|i have|let me)\s+(?:send|deliver|upload)\b.{0,30}"
                                             r"\b(?:file|files|logo|design|thumbnail|draft)", text, re.I):
        return "you are the client: the freelancer delivers the files, not you."
    if me["role"] == "freelancer" and re.search(r"\b(?:i'?ve|i have|just)\s+(?:re-?uploaded|re-?sent|updated|fixed|revised|"
                                                r"replaced|sent (?:a|the|you a) (?:new|revised|updated|fixed))", text, re.I):
        n = db.one(conn, "SELECT COUNT(*) n FROM delivery WHERE room_id=?", room["id"])["n"]
        recent = db.one(conn, "SELECT 1 FROM message WHERE room_id=? AND sender_id=? AND kind='chat' AND text LIKE '📎%' "
                              "AND id > (SELECT COALESCE(MAX(id), 0) FROM message WHERE room_id=? AND kind='chat' AND sender_id!=?)",
                        room["id"], me["id"], room["id"], me["id"])
        if n < 2 or not recent:
            return "you haven't delivered a new version: don't say you did. Say what you'll change instead."
    from .protect import OFF_PLATFORM, REFUND_ASK
    if not scam_turn and (OFF_PLATFORM.search(text) or REFUND_ASK.search(text) or
                          re.search(r"wallet|crypto|bitcoin|\bbtc\b|gift ?card", text, re.I)):
        return "never agree to or suggest any payment or refund outside the PayPal button; refuse kindly."
    others = [m["text"] for m in R.thread_text(conn, room, limit=8) if m["name"] != me["name"]]
    mine = [m["text"] for m in R.thread_text(conn, room, limit=8) if m["name"] == me["name"]]
    if re.match(r"^\s*\w+(?: \(AI\))?\s*:", text) or any(_similar(text, o) for o in others[-3:]):
        return "write your own reply, don't repeat or quote the other person."
    if any(_similar(text, o) for o in mine[-3:]):
        return "you already said that: say something new, or nothing."
    promise = re.search(r"i'?ll (?:send|pay|make)\b[^.!?]{0,30}", text.lower())   # "I'll send the PayPal payment right away", again
    if promise and sum(promise.group(0)[:22] in o.lower().replace("\u2019", "'") for o in mine[-4:]) >= 1:
        return "you already promised that: don't repeat it; say something new, or nothing."
    t = T.latest(conn, room["id"])
    cfg = AI.get(me["key"]) or {}
    if t and t["agreed"]:
        agreed = " ".join(map(str, t["data"].get("deliverables") or [])).lower() + " " + str(t["data"].get("item", "")).lower()
        stale = [w for w in cfg.get("wish_words", []) if re.search(rf"\b{w}\b", text, re.I) and w not in agreed]
        if stale:
            return (f"the accepted terms don't include {', '.join(stale)}: ask for what the terms say "
                    f"({', '.join(map(str, t['data'].get('deliverables') or []))}), not your first wishes.")
    if t and t["agreed"] and RENEGOTIATE.search(text):
        return "both already accepted the terms: don't renegotiate or ask to accept them again."
    if LINK.search(text) and not scam_turn:
        return "payments only happen with the PayPal button on the deal card: no links or emails."
    return None


def _reply(conn, room, lock, extra: str = ""):
    key, cfg, me = who(conn, room)
    chat = R.thread_text(conn, room, limit=20)
    mine = [m for m in chat if m["name"] == me["name"]]
    scam_turn = False
    if not extra and len(mine) == 2 and not any(cfg["scam_mark"] in m["text"].lower() for m in mine):
        extra, scam_turn = cfg["scam"], True
    convo = "\n".join(f"{m['name']}: {m['text']}" for m in chat)
    t = T.latest(conn, room["id"])
    wishes = "" if t and t["agreed"] else (" " + cfg.get("wishes", ""))
    system = cfg["persona"] + wishes + " " + RULES + (" " + extra if extra else "") + \
        "\nReply JSON only: {\"message\": \"your next message\"}"
    ask = f"Deal room state (the truth, from the record):\n{_state(conn, room, me)}\n\nChat so far:\n{convo}\n\nYour next message:"
    text = ""
    for _ in range(3):   # two retries if the message contradicts the record; otherwise the AI stays silent
        out = llm.ask(system, ask, task="fast", max_tokens=160)
        text = str((out or {}).get("message") or "").strip()
        why = problem(conn, room, me, text, scam_turn) if text else None
        if not why:
            break
        if os.environ.get("DEALROOM_AI_DEBUG"):
            print(f"[ai_people] {me['name']} draft refused ({why}): {text}", flush=True)
        ask += f"\n\n(Your draft \"{text}\" was wrong: {why} Write a different message.)"
        text = ""
    if text:
        mid = R.say(conn, room["id"], me["id"], text[:400])
        from . import protect as P, watch as WA
        P.scan(conn, room, me, text)
        if WA.check(conn, R.get(conn, room["id"]), mid, me) == "drafted":
            react(conn, lock, room["id"], "terms")


def _deliver(conn, room, me, cfg):
    sample, name = cfg["delivers"]

    class Upload:   # the same path as a person's upload
        filename = name

        def read(self):
            return (db.ROOT / "samples" / sample).read_bytes()
    return D.save(conn, room, me, Upload())


DESIGN = ("You are a thumbnail designer making a revision. From the agreed terms and the client's requests, choose the "
          "design. Reply JSON only: {\"background\": \"#RRGGBB\", \"lines\": [up to 3 short title lines, exactly the "
          "words the client asked for if they asked for a text], \"text_color\": \"#RRGGBB\"}. Pick colours that read well.")


def _draw_revision(conn, room, me, cfg) -> int | None:
    """Rafael's revision: the same dog, with the background and the title the client asked for."""
    from PIL import Image, ImageDraw, ImageFont
    import io
    t = T.latest(conn, room["id"])["data"]
    asks = "\n".join(f"- {m['text']}" for m in R.thread_text(conn, room, limit=30) if m["name"] != me["name"])
    out = llm.ask(DESIGN, f"Terms: {json.dumps({k: t.get(k) for k in ('item', 'deliverables')})}\nClient said:\n{asks}",
                  task="smart", max_tokens=200) or {}
    hexc = lambda v, d: v if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v) else d
    bg, fg = hexc(out.get("background"), "#FFD23F"), hexc(out.get("text_color"), "#141414")
    lines = [str(x)[:16].upper() for x in (out.get("lines") or ["MY DOG"])][:3]
    W, H = 1280, 720
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    fur, dark = (214, 160, 98), (120, 78, 40)
    d.ellipse([820, 180, 1160, 520], fill=fur); d.ellipse([770, 200, 880, 440], fill=dark)
    d.ellipse([1100, 200, 1210, 440], fill=dark); d.ellipse([900, 300, 940, 340], fill=(30, 30, 30))
    d.ellipse([1040, 300, 1080, 340], fill=(30, 30, 30)); d.ellipse([950, 360, 1030, 440], fill=(40, 30, 30))
    path = str(db.ROOT / "dealroom" / "fonts" / "DejaVuSans-Bold.ttf")
    size = 110 if max(len(x) for x in lines) <= 9 else 80
    f = ImageFont.truetype(path, size)
    y = (H - len(lines) * (size + 20)) // 2
    for line in lines:
        d.text((70, y), line, fill=fg, font=f)
        y += size + 20
    buf = io.BytesIO(); im.save(buf, "PNG")
    n = db.one(conn, "SELECT COUNT(*) c FROM delivery WHERE room_id=?", room["id"])["c"] + 1

    class Upload:
        filename = f"thumbnail_v{n}.png"

        def read(self):
            return buf.getvalue()
    return D.save(conn, room, me, Upload())


def _wants_revision(conn, room, me, cfg) -> bool:
    """A freelancer AI revises when its last file failed the check, the client spoke since, and revisions remain."""
    if me["role"] != "freelancer" or "delivers" not in cfg:
        return False
    dv = D.latest(conn, room["id"])
    t = T.latest(conn, room["id"])
    if not dv or not t or not any(c["result"] == "no" for c in dv["check"].get("checks") or []):
        return False
    done = db.one(conn, "SELECT COUNT(*) c FROM delivery WHERE room_id=?", room["id"])["c"] - 1
    last = db.one(conn, "SELECT MAX(m.id) m FROM message m JOIN person p ON p.id=m.sender_id WHERE m.room_id=? "
                        "AND m.kind='chat' AND p.id!=?", room["id"], me["id"])["m"] or 0
    since = db.one(conn, "SELECT MAX(id) m FROM message WHERE room_id=? AND kind='chat' AND data LIKE ?", room["id"],
                   f'%"delivery_id": {dv["id"]}%')["m"] or 0   # the file itself in the chat, not the notes about it
    return last > since and done < int(t["data"].get("revisions") or 0)


def react(conn, lock, room_id: int, event: str):
    """Run the AI person's turn in the background, so nobody's screen waits for the AI."""
    first = R.get(conn, room_id)   # callers hold the lock; no thread at all for rooms without an AI person
    if not first or not who(conn, first) or not llm.available():
        return

    def run():
        with lock:
            room = R.get(conn, room_id)
            if not room:
                return
            key, cfg, me = who(conn, room)
            t = T.latest(conn, room_id)
            if event == "message":
                if _wants_revision(conn, room, me, cfg) and _draw_revision(conn, room, me, cfg):
                    _reply(conn, room, lock, "You just uploaded a revised file on the deal card, made from what the client "
                                             "asked. Say so in one sentence and ask them to check it. Everything is paid.")
                else:
                    _reply(conn, room, lock)
            elif event == "terms" and t and not t["agreed"]:
                fair = cfg["fair"](t["data"])
                if fair:
                    T.accept(conn, room, me)
                _reply(conn, room, lock, "The terms just appeared on the deal card. " + (
                    "They match what you wanted: say you accepted them." if fair else
                    f"You haven't accepted them: the price is {cfg['unfair']}. Say what you'd accept."))
                react(conn, lock, room_id, "accepted")
            elif event == "accepted" and t and t["agreed"] and cfg["role"] == "freelancer" and not t["invoice_number"]:
                if B.make_invoice(conn, room):
                    _reply(conn, room, lock, "You just made the invoice from the accepted terms. Say the PayPal button "
                                             "is now on the deal card, and you'll deliver once it's paid.")
            elif event == "invoice" and cfg["role"] == "client":
                _reply(conn, room, lock, "The freelancer just made the invoice. Say you'll pay with the PayPal button.")
                if not db.one(conn, "SELECT 1 FROM message WHERE room_id=? AND text LIKE ?", room_id, f"{me['name']} is played by%"):
                    R.note(conn, room_id, "info", f"{me['name']} is played by AI, and PayPal needs a real person to approve a "
                           "payment, even in the sandbox. To pay for real, log in as Alex: Maya's portrait is waiting for "
                           "his last $40.", only_for=room["freelancer_id"])
            elif event == "paid" and cfg["role"] == "freelancer" and not D.latest(conn, room_id):
                if _deliver(conn, room, me, cfg):
                    _reply(conn, room, lock, "You were just paid and you delivered the file on the deal card. Say so and "
                                             "ask the client to check it.")
            elif event == "delivery" and cfg["role"] == "client":
                dv = D.latest(conn, room_id)
                if dv:
                    D.download(conn, room, me, dv["id"], "ai-client")
                    checks = dv["check"].get("checks") or []
                    bad = [c["requirement"] for c in checks if c["result"] == "no"]
                    due = any(not p["paid"] for p in B.parts(conn, room))
                    if not checks:   # no AI check on record: don't pretend to love a file nobody looked at
                        _reply(conn, room, lock, "You received the file on the deal card. Thank them and say you'll look "
                               "at it closely against what you agreed; don't say whether it's good yet, and don't pay yet.")
                        return
                    _reply(conn, room, lock, (
                        "You opened the file. The agreed terms require: " + ", ".join(bad) + ", and the file doesn't have it ("
                        + (dv["check"].get("seen") or "") + "). Politely ask for a fix that matches the agreed terms, "
                        "using one of the revisions.") if bad else
                        ("You opened the file and love it. Thank them and say you'll pay what's left with the PayPal button."
                         if due else "You opened the file and love it. Thank them; everything is already paid."))
    threading.Thread(target=run, daemon=True).start()
