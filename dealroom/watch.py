"""The room keeps up with the chat: when people agree, change the deal or ask for new work, it notices.

NVIDIA Nemotron Nano only says what kind of message it is. Code decides what happens: terms are
re-drafted only before anything is paid (and kept only if a value really changed), and new work after
a deal is under way becomes its own deal, with its own terms, invoice and record.
"""

from __future__ import annotations

import json
import threading

from . import billing as B, db, delivery as D, llm, room as R, terms as T

KINDS = ("agreed", "change", "new_work", "none")
WATCH = (
    "You read a chat between a freelancer and a client in a deal room. Classify ONLY the LAST message, using the "
    "current deal and the chat before it as context. Reply JSON only: {\"kind\": one of\n"
    "\"agreed\": there are no terms yet, and this message completes an agreement on the work and its price "
    "(for example the freelancer confirms the price and payment plan the client asked about).\n"
    "\"change\": there are terms, and this message asks to change one of them for the SAME piece of work "
    "(its price, deposit, revisions, deadline, or a detail such as a color).\n"
    "\"new_work\": this message asks for, offers or prices ANOTHER piece of work beyond the current deal "
    "(another portrait, a second item, \"the new project\"), even if the details differ.\n"
    "\"none\": anything else. Questions, answers to questions, thanks, praise, asking where the invoice or file "
    "is, complaints and threats are none.\n"
    "\"title\": if new_work, a short name for the new work (2 to 5 words), else null}"
)


def _deal(conn, room):
    t = T.latest(conn, room["id"])
    if not t:
        return "No terms yet.", t
    d = t["data"]
    return json.dumps({k: d.get(k) for k in ("item", "deliverables", "price", "deposit", "revisions", "deadline")}), t


def classify(conn, room, msg_id: int) -> dict:
    chat = R.chat_text(conn, room["id"], limit=12)
    if not chat or chat[-1]["id"] != msg_id:
        chat = [m for m in chat if m["id"] <= msg_id]
    if not chat:
        return {"kind": "none"}
    deal, _ = _deal(conn, room)
    lines = "\n".join(f"{m['name']} ({m['role']}): {m['text']}" for m in chat[:-1])
    last = chat[-1]
    out = llm.ask(WATCH, f"Current deal: {deal}\n\nChat before:\n{lines or '(nothing)'}\n\n"
                         f"LAST message, {last['name']} ({last['role']}): {last['text']}", task="smart", max_tokens=120) or {}
    kind = out.get("kind") if out.get("kind") in KINDS else "none"
    title = str(out.get("title") or "").strip()[:60] or "New job"
    return {"kind": kind, "title": title}


def under_way(conn, room) -> bool:
    """Once money moved or a file was delivered, the agreed terms are part of the record and stay as they are."""
    return any(p["paid"] for p in B.parts(conn, room)) or D.latest(conn, room["id"]) is not None


def check(conn, room, msg_id: int, sender) -> str | None:
    """Run after a chat message. Returns what was done: 'drafted', 'suggested', or None."""
    if not llm.available():
        return None
    c = classify(conn, room, msg_id)
    t = T.latest(conn, room["id"])
    kind = c["kind"]
    open_ = suggestion(room)
    if kind == "new_work" and t:
        if open_:   # already offered: keep where it started, take the more precise name, say nothing twice
            set_suggestion(conn, room, {"title": c["title"], "from": open_["from"]})
            return "suggested"
        set_suggestion(conn, room, {"title": c["title"], "from": msg_id})
        R.note(conn, room["id"], "info", f"New work came up in the chat: \"{c['title']}\". It gets its own deal, with its "
               "own terms, invoice and record. Start it from the deal card.")
        return "suggested"
    if open_:   # they're talking about the new work: this deal's terms aren't what's changing
        return None
    if kind == "agreed" and t:   # agreeing again after terms exist: the same as a possible change
        kind = "change"
    if kind == "agreed" or kind == "change" and t and not under_way(conn, room):
        why = "Your chat now has the work and a price" if not t else "The chat changed the deal"
        if T.draft(conn, room["id"], why=why, only_if_changed=bool(t)):
            return "drafted"
        return None
    if kind == "change" and t and under_way(conn, room):
        rev = t["data"].get("revisions")
        R.note(conn, room["id"], "info", "This deal is already under way, so its terms stay as agreed. A fix to the same "
               "work uses one of the revisions" + (f" ({rev} included)" if rev is not None else "") +
               "; new work gets its own deal.")
    return None


def after_message(conn, lock, room_id: int, msg_id: int, sender_id: int, then=None):
    """In the background, so nobody waits for the AI: check the message, then run `then` (e.g. the AI client)."""
    def run():
        with lock:
            room = R.get(conn, room_id)
            if room:
                did = check(conn, room, msg_id, R.person_by_id(conn, sender_id))
                if then:
                    then(did)
    threading.Thread(target=run, daemon=True).start()


def suggestion(room) -> dict | None:
    return db.jload(room["suggest"]) if "suggest" in room.keys() and room["suggest"] else None


def set_suggestion(conn, room, value: dict | None):
    db.update(conn, "room", room["id"], suggest=json.dumps(value) if value else None)


def start_new_deal(conn, room) -> int | None:
    """New work becomes its own room: same two people, the messages about it copied over, terms drafted."""
    s = suggestion(room)
    if not s:
        return None
    rid = db.insert(conn, "room", title=s["title"][:60], freelancer_id=room["freelancer_id"],
                    client_id=room["client_id"], status="talking", story=room["story"], created=db.now(),
                    parent_id=room["id"])
    rows = db.all_(conn, "SELECT * FROM message WHERE room_id=? AND kind='chat' AND id>=? ORDER BY id", room["id"], s["from"])
    for m in rows:
        db.insert(conn, "message", room_id=rid, sender_id=m["sender_id"], kind="chat", text=m["text"], ts=m["ts"])
    R.note(conn, rid, "info", f"New deal, started from \"{room['title']}\". The messages about this job were copied "
           "here; everything from now on is recorded in this deal.")
    R.note(conn, room["id"], "good", f"A new deal was started for \"{s['title']}\". This deal stays as it was agreed.")
    set_suggestion(conn, room, None)
    return rid
