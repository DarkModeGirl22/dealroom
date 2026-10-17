"""Terms: the AI turns the chat into a contract; code checks every value against what was said."""

from __future__ import annotations

import json
import re

from . import db, llm, room as R

SYSTEM = (
    "You read a chat between a freelancer and a client and write the terms they agreed on, as JSON. "
    "Only use what the chat says. Never invent a price, date or number. If something was not agreed, use null. "
    "If an earlier deal is given and the chat refers to it (\"same price\", \"like last time\"), use the earlier "
    "deal's value. A deadline can be relative, written as said (\"2 days\"). "
    "Reply JSON only: {\"item\": short name of the work, \"deliverables\": [what will be delivered, including the specific details the client asked for, such as subject, colors, background, size or format], "
    "\"price\": total price as a number or null, \"deposit\": amount paid before work starts or null, "
    "\"revisions\": number of revisions included or null, \"deadline\": delivery date or day as written or null, "
    "\"refund_policy\": refund rule as agreed or null, "
    "\"quotes\": {\"price\": message id where it was said, \"deposit\": id, \"revisions\": id, \"deadline\": id}, "
    "\"missing\": [what they still need to agree on]}"
)
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "half": None}


def _numbers_in(text: str) -> set[float]:
    found = {float(x.replace(",", "")) for x in re.findall(r"\d+(?:[.,]\d+)?", text)}
    for w, n in WORDS.items():
        if n is not None and re.search(rf"\b{w}\b", text, re.I):
            found.add(float(n))
    return found


def check(draft: dict, chat: list[dict], earlier: dict | None = None) -> tuple[dict, list[str]]:
    """Keep a value only if the chat supports it (or the earlier deal it refers to, e.g. "same price").
    Returns (terms, notes on what was dropped)."""
    allt = "\n".join(m["text"] for m in chat)
    if earlier:
        allt += "\n" + " ".join(str(earlier.get(k)) for k in ("price", "deposit", "revisions", "deadline") if earlier.get(k) is not None)
    nums = _numbers_in(allt)
    out, dropped = {}, []
    out["item"] = (draft.get("item") or "Work")[:80]
    out["deliverables"] = [str(d)[:120] for d in (draft.get("deliverables") or [])][:6]
    for k in ("price", "deposit", "revisions"):
        v = draft.get(k)
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        if v is None:
            out[k] = None
            continue
        ok = v in nums
        if k == "deposit" and not ok and out.get("price") and abs(v - out["price"] / 2) < 0.01 \
                and re.search(r"\bhalf\b|50 ?%", allt, re.I):
            ok = True
        if k == "deposit" and out.get("price") and v > out["price"]:
            ok = False
        if k == "deposit" and ok and out.get("price") and abs(v - out["price"]) < 0.01:
            out[k] = None   # "everything upfront" is one full payment, not a deposit (found by tools/eval.py)
            continue
        if ok:
            out[k] = int(v) if k == "revisions" else round(v, 2)
        else:
            out[k] = None
            dropped.append(k)
    out["deadline"] = (draft.get("deadline") or None)
    if out["deadline"] and not any(w.lower() in allt.lower() for w in re.findall(r"[A-Za-z0-9]+", out["deadline"])[:3]):
        out["deadline"], _ = None, dropped.append("deadline")
    out["refund_policy"] = draft.get("refund_policy") or None
    out["quotes"] = {k: v for k, v in (draft.get("quotes") or {}).items() if out.get(k) is not None}
    return out, dropped


def _earlier(conn, room_id) -> dict | None:
    """The agreed terms of the deal this one was started from, if any."""
    r = db.one(conn, "SELECT parent_id FROM room WHERE id=?", room_id)
    t = latest(conn, r["parent_id"]) if r and r["parent_id"] else None
    return t["data"] if t and t["agreed"] else None


def latest(conn, room_id):
    t = db.one(conn, "SELECT * FROM terms WHERE room_id=? ORDER BY version DESC LIMIT 1", room_id)
    if not t:
        return None
    d = dict(t)
    d["data"] = db.jload(t["data"], {})
    d["agreed"] = bool(t["accepted_freelancer"] and t["accepted_client"])
    return d


def _words(d: dict) -> set:
    return set(re.findall(r"[a-z0-9]+", " ".join(map(str, d.get("deliverables") or [])).lower())) - STOP


STOP = {"a", "an", "the", "of", "on", "with", "in", "and", "to", "for", "digital", "portrait", "file"}


def same(a: dict, b: dict) -> bool:
    """Same deal: the same numbers and deadline, and no new detail in the deliverables."""
    keys = ("price", "deposit", "revisions")
    if any(a.get(k) != b.get(k) for k in keys):
        return False
    if (a.get("deadline") or "").lower() != (b.get("deadline") or "").lower():
        return False
    return _words(b) <= _words(a)


def draft(conn, room_id, why: str | None = None, only_if_changed: bool = False) -> dict | None:
    chat = R.chat_text(conn, room_id)
    if not chat:
        return None
    lines = "\n".join(f"[{m['id']}] {m['name']} ({m['role']}): {m['text']}" for m in chat)
    earlier = _earlier(conn, room_id)
    if earlier:
        lines = ("Earlier deal between the same two people, for references like \"same price\": " +
                 json.dumps({k: earlier.get(k) for k in ("item", "price", "deposit", "revisions", "deadline")}) +
                 "\n\nChat about this new deal:\n" + lines)
    raw = llm.ask(SYSTEM, lines, task="smart", max_tokens=600)
    if not raw:
        R.note(conn, room_id, "warn", "DealRoom couldn't read the terms right now. Please write them on the deal card.")
        return None
    terms, dropped = check(raw, chat, earlier)
    prev = latest(conn, room_id)
    if only_if_changed and prev and same(prev["data"], terms):
        return None
    version = (prev["version"] + 1) if prev else 1
    db.insert(conn, "terms", room_id=room_id, version=version, data=json.dumps(terms), source="ai", ts=db.now())
    bits = [f"{terms['item']}"]
    if terms["price"] is not None:
        bits.append(f"${terms['price']:g}")
    if terms["deposit"]:
        bits.append(f"${terms['deposit']:g} deposit")
    if terms["revisions"] is not None:
        bits.append(f"{terms['revisions']} revisions")
    if terms["deadline"]:
        bits.append(f"due {terms['deadline']}")
    msg = (f"{why}, so " if why else "") + ("terms drafted" if not why else "here are the terms, drafted") + \
        " from your chat by " + llm.LABELS["smart"] + ": " + ", ".join(bits) + ". Both of you, check and accept on the deal card."
    msg = msg[0].upper() + msg[1:]
    if dropped:
        msg += " Not clear from the chat: " + ", ".join(dropped) + "."
    missing = [m for m in (raw.get("missing") or []) if isinstance(m, str)][:3]
    if terms["price"] is None:
        missing = ["the price"] + missing
    R.note(conn, room_id, "info" if not missing else "warn", msg + (" Still to agree: " + "; ".join(missing) + "." if missing else ""))
    return terms


def edit(conn, room_id, person, form) -> None:
    prev = latest(conn, room_id)
    data = dict(prev["data"]) if prev else {"item": "Work", "deliverables": []}
    for k in ("price", "deposit"):
        try:
            data[k] = round(float(form.get(k)), 2) if form.get(k) not in (None, "") else None
        except ValueError:
            pass
    if form.get("revisions") not in (None, ""):
        try:
            data["revisions"] = int(form.get("revisions"))
        except ValueError:
            pass
    for k in ("item", "deadline", "refund_policy"):
        if form.get(k) is not None:
            data[k] = form.get(k).strip()[:120] or None
    version = (prev["version"] + 1) if prev else 1
    db.insert(conn, "terms", room_id=room_id, version=version, data=json.dumps(data), source=f"edited by {person['name']}",
              ts=db.now())
    R.note(conn, room_id, "info", f"{person['name']} edited the terms (version {version}). Both of you need to accept again.")


def accept(conn, room, person) -> None:
    t = latest(conn, room["id"])
    if not t or t["data"].get("price") is None:
        return
    col = "accepted_freelancer" if person["id"] == room["freelancer_id"] else "accepted_client"
    if t[col]:
        return
    db.update(conn, "terms", t["id"], **{col: db.now()})
    t = latest(conn, room["id"])
    if t["agreed"]:
        db.update(conn, "room", room["id"], status="agreed")
        R.note(conn, room["id"], "good", f"Both accepted the terms (version {t['version']}). They're now part of the record.")
    else:
        R.note(conn, room["id"], "info", f"{person['name']} accepted the terms. Waiting for the other side.")
