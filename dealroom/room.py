"""The room: messages, notes, and the record everything else is built on."""

from __future__ import annotations

from . import db

DEMO_PEOPLE = [
    ("maya", "Maya", "freelancer", "maya.sample@example.com"),
    ("alex", "Alex", "client", "alex.sample@example.com"),
    ("lina", "Lina", "client", "lina.sample@example.com"),
    ("sam", "Sam", "client", "sam.sample@example.com"),
]
STORIES_ORDER = ("ai", "wrong", "new")
STORIES = {
    "new": {"title": "A new deal", "line": "Maya draws pet portraits; Alex wants one of his dog. Play both sides, the "
            "seller and the buyer: talk, agree, pay with PayPal (sandbox), deliver."},
    "ai": {"title": "A new deal, with an AI client", "line": "You're Maya. Alex is played by NVIDIA Nemotron: he chats, "
           "accepts fair terms, tries one scam and checks your delivery. Switch to Alex only to pay with PayPal."},
    "wrong": {"title": "A deal gone wrong", "line": "Alex got his portrait two days ago and it was paid in full. Now he "
              "claims he never received it. Write it as Alex and see what the room does."},
}


STORIES = {k: STORIES[k] for k in STORIES_ORDER}


def _ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def seed(conn, story: str = "new"):
    """Start (or restart) a story in this visitor's private copy."""
    for t in ("download", "delivery", "payment", "terms", "message", "dispute", "room", "person"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    ids = {k: db.insert(conn, "person", key=k, name=("Alex (AI)" if story == "ai" and k == "alex" else n), role=r, email=e)
           for k, n, r, e in DEMO_PEOPLE}
    rid = db.insert(conn, "room", title="Dog portrait", freelancer_id=ids["maya"], client_id=ids["alex"],
                    status="talking", story=story, created=_ago(72 if story == "wrong" else 0))
    if story == "wrong":
        _seed_wrong(conn, rid, ids)
    if story == "ai":
        db.insert(conn, "message", room_id=rid, sender_id=ids["alex"], kind="chat", ts=db.now(),
                  text="Hi Maya! I love your pet portraits. Could you draw my dog Biscuit on a blue background?")
    _seed_others(conn, ids)
    return rid


def _seed_others(conn, ids):
    """Maya's other conversations, so the list looks like a real inbox: a finished PDF job and a new request."""
    import hashlib
    import json
    import shutil
    m, lina, sam = ids["maya"], ids["lina"], ids["sam"]
    r = db.insert(conn, "room", title="CV translation", freelancer_id=m, client_id=lina, status="agreed", story="sample",
                  created=_ago(200))
    for h, who, text in [(200, lina, "Hello Maya, could you translate my CV from French to English? It's 2 pages."),
                         (199.5, m, "Of course: $40, 1 revision, ready in 3 days."),
                         (199, lina, "Perfect, thank you!")]:
        db.insert(conn, "message", room_id=r, sender_id=who, kind="chat", text=text, ts=_ago(h))
    terms = {"item": "CV translation", "deliverables": ["CV translated from French to English", "2 pages"],
             "price": 40.0, "deposit": None, "revisions": 1, "deadline": "3 days", "refund_policy": None}
    db.insert(conn, "terms", room_id=r, version=1, data=json.dumps(terms), source="ai", accepted_freelancer=_ago(198.8),
              accepted_client=_ago(198.7), invoice_number="DR-SAMPLE-2", ts=_ago(198.9))
    db.insert(conn, "payment", room_id=r, part="full", amount=40.0, order_id="SAMPLE-ORDER-3", capture_id="SAMPLE-CAPTURE-3",
              status="COMPLETED", payer_email="lina.sample@example.com", ts=_ago(198.5), paid_ts=_ago(198.4))
    src = db.ROOT / "samples" / "cv_lina_english.pdf"
    raw = src.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    stored = db.UPLOADS / f"sample-{sha[:12]}.pdf"
    if not stored.exists():
        shutil.copy(src, stored)
    check = {"seen": "A two-page CV for Lina Haddad, in English.",
             "checks": [{"requirement": "Translated to English", "result": "yes", "evidence": "\"Marketing assistant with three years of experience\""},
                        {"requirement": "2 pages", "result": "yes", "evidence": "the PDF has 2 pages"}]}
    did = db.insert(conn, "delivery", room_id=r, filename="CV_Lina_Haddad_EN.pdf", stored=str(stored), sha256=sha,
                    check_data=json.dumps(check), ts=_ago(150))
    db.insert(conn, "download", delivery_id=did, person_id=lina, ip="sample", ts=_ago(149))
    for h, lvl, who, text in [(198.7, "good", None, "Both accepted the terms (version 1). They're now part of the record."),
                              (198.4, "good", None, "Payment of $40.00 received through PayPal. It matches the terms exactly. (sample)"),
                              (150, "good", None, "NVIDIA Nemotron 3 Super checked the file against the terms: Translated to English ✓; 2 pages ✓."),
                              (149, "good", None, "Lina downloaded \"CV_Lina_Haddad_EN.pdf\". Recorded in the room."),
                              (148, None, lina, "Got it, it's perfect. Thanks!")]:
        db.insert(conn, "message", room_id=r, sender_id=who, kind="chat" if who else "note", level=lvl, text=text, ts=_ago(h))
    r = db.insert(conn, "room", title="Bakery logo", freelancer_id=m, client_id=sam, status="talking", story="sample",
                  created=_ago(5))
    for h, who, text in [(5, sam, "Hi! I'm opening a small bakery. Could you design a logo? Something warm, with a croissant."),
                         (4.8, m, "I'd love to! Do you have colors in mind?"),
                         (4.5, sam, "Brown and cream. What would it cost?")]:
        db.insert(conn, "message", room_id=r, sender_id=who, kind="chat", text=text, ts=_ago(h))


def _seed_wrong(conn, rid, ids):
    """A complete, paid, delivered deal, recorded as samples (PayPal needs a human to approve real payments)."""
    import hashlib
    import json
    import shutil
    m, a = ids["maya"], ids["alex"]
    chat = [(70, a, "Hi Maya! Could you draw my dog Biscuit? Blue background please."),
            (69.5, m, "Sure! A digital portrait is $80 with 2 revisions included. I can deliver Friday."),
            (69, a, "Great. Can I pay half now?"),
            (68.8, m, "Yes: $40 deposit now and $40 when I deliver the file.")]
    terms = {"item": "Digital portrait of Biscuit", "deliverables": ["digital portrait"], "price": 80.0, "deposit": 40.0,
             "revisions": 2, "deadline": "Friday", "refund_policy": None}
    db.insert(conn, "terms", room_id=rid, version=1, data=json.dumps(terms), source="ai", accepted_freelancer=_ago(68.5),
              accepted_client=_ago(68.4), invoice_number="DR-SAMPLE-1", ts=_ago(68.6))
    db.insert(conn, "payment", room_id=rid, part="deposit", amount=40.0, order_id="SAMPLE-ORDER-1",
              capture_id="SAMPLE-CAPTURE-1", status="COMPLETED", payer_email="alex.sample@example.com", ts=_ago(68.3),
              paid_ts=_ago(68.2))
    src = db.ROOT / "samples" / "biscuit_portrait_blue.png"
    raw = src.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    stored = db.UPLOADS / f"sample-{sha[:12]}.png"
    if not stored.exists():
        shutil.copy(src, stored)
    check = {"seen": "A digital portrait of a light brown dog with floppy ears and a red collar, on a solid blue background.",
             "checks": [{"requirement": "Dog portrait", "result": "yes", "evidence": "a dog is the subject"},
                        {"requirement": "Blue background", "result": "yes", "evidence": "solid blue background"}]}
    did = db.insert(conn, "delivery", room_id=rid, filename="biscuit_portrait.png", stored=str(stored), sha256=sha,
                    check_data=json.dumps(check), ts=_ago(49))
    db.insert(conn, "download", delivery_id=did, person_id=a, ip="sample", ts=_ago(48.5))
    db.insert(conn, "payment", room_id=rid, part="final", amount=40.0, order_id="SAMPLE-ORDER-2",
              capture_id="SAMPLE-CAPTURE-2", status="COMPLETED", payer_email="alex.sample@example.com", ts=_ago(48.4),
              paid_ts=_ago(48.3))
    notes = [(68.6, "info", "Terms drafted from your chat by NVIDIA Nemotron 3 Super: Digital portrait of Biscuit, $80, $40 deposit, 2 revisions, due Friday."),
             (68.4, "good", "Both accepted the terms (version 1). They're now part of the record."),
             (68.2, "good", "Deposit of $40.00 received through PayPal. It matches the terms exactly. (sample)"),
             (49, "good", "NVIDIA Nemotron 3.5 Super VL checked the file against the terms: Dog portrait ✓; Blue background ✓."),
             (48.5, "good", "Alex downloaded \"biscuit_portrait.png\". Recorded in the room."),
             (48.3, "good", "Final payment of $40.00 received through PayPal. Everything in the terms is paid. (sample)")]
    chat.append((48, a, "Got it, love it! Thanks Maya."))
    feed = [(h, who, "chat", None, text) for h, who, text in chat] + [(h, None, "note", lvl, text) for h, lvl, text in notes]
    for h, who, kind, lvl, text in sorted(feed, key=lambda x: -x[0]):   # oldest first, as it happened
        db.insert(conn, "message", room_id=rid, sender_id=who, kind=kind, level=lvl, text=text, ts=_ago(h))


def person(conn, key: str):
    return db.one(conn, "SELECT * FROM person WHERE key=?", key)


def person_by_id(conn, pid):
    return db.one(conn, "SELECT * FROM person WHERE id=?", pid)


def thread(conn, room):
    """All deals between the same two people: one conversation, like a chat app."""
    return db.all_(conn, "SELECT * FROM room WHERE freelancer_id=? AND client_id=? ORDER BY id",
                   room["freelancer_id"], room["client_id"])


def conversations(conn, p):
    """The viewer's conversations, one per person, most recent first, with the last message."""
    out = {}
    for r in rooms_for(conn, p):
        other_id = r["client_id"] if r["freelancer_id"] == p["id"] else r["freelancer_id"]
        out.setdefault(other_id, []).append(r)
    rows = []
    for other_id, rooms in out.items():
        ids = [r["id"] for r in rooms]
        last = db.one(conn, f"""SELECT m.text, m.ts, m.kind FROM message m WHERE m.room_id IN ({",".join("?" * len(ids))})
                               AND m.kind='chat' ORDER BY m.ts DESC, m.id DESC LIMIT 1""", *ids)
        rows.append({"other": person_by_id(conn, other_id), "room": rooms[-1], "deals": len(rooms),
                     "last": last["text"] if last else "", "ts": last["ts"] if last else rooms[-1]["created"]})
    return sorted(rows, key=lambda x: x["ts"], reverse=True)


def rooms_for(conn, p):
    return db.all_(conn, """SELECT r.*, f.name AS freelancer, c.name AS client FROM room r
                            JOIN person f ON f.id=r.freelancer_id JOIN person c ON c.id=r.client_id
                            WHERE r.freelancer_id=? OR r.client_id=? ORDER BY r.id""", p["id"], p["id"])


def get(conn, room_id):
    return db.one(conn, """SELECT r.*, f.name AS freelancer, c.name AS client, f.key AS freelancer_key,
                           c.key AS client_key FROM room r JOIN person f ON f.id=r.freelancer_id
                           JOIN person c ON c.id=r.client_id WHERE r.id=?""", room_id)


def side(room, p) -> str | None:
    if p["id"] == room["freelancer_id"]:
        return "freelancer"
    if p["id"] == room["client_id"]:
        return "client"
    return None


def other(conn, room, p):
    return person_by_id(conn, room["client_id"] if p["id"] == room["freelancer_id"] else room["freelancer_id"])


def say(conn, room_id, sender_id, text: str) -> int:
    return db.insert(conn, "message", room_id=room_id, sender_id=sender_id, kind="chat", text=text.strip(),
                     ts=db.now())


def note(conn, room_id, level: str, text: str, data: dict | None = None, only_for: int | None = None) -> int:
    """A note from DealRoom in the room. Shared with both people, unless only_for is set (private)."""
    import json
    return db.insert(conn, "message", room_id=room_id, sender_id=None, kind="private" if only_for else "note",
                     level=level, only_for=only_for, text=text, data=json.dumps(data) if data else None,
                     ts=db.now())


def feed(conn, room_id, viewer_id, after: int = 0):
    """The whole conversation with this person (every deal), in the order it happened, minus others' private notes."""
    room = get(conn, room_id)
    ids = [r["id"] for r in thread(conn, room)]
    rows = db.all_(conn, f"""SELECT m.*, p.name AS sender, p.key AS sender_key, r.title AS deal FROM message m
                            LEFT JOIN person p ON p.id=m.sender_id JOIN room r ON r.id=m.room_id
                            WHERE m.room_id IN ({",".join("?" * len(ids))}) AND m.id>?
                            AND (m.only_for IS NULL OR m.only_for=?) ORDER BY m.ts, m.id""", *ids, after, viewer_id)
    return [dict(r) | {"data": db.jload(r["data"])} for r in rows]


def thread_text(conn, room, limit: int = 30) -> list[dict]:
    """The whole conversation between the two people, as the AI client reads it."""
    ids = [r["id"] for r in thread(conn, room)]
    rows = db.all_(conn, f"""SELECT m.id, m.text, m.ts, p.name, p.role FROM message m JOIN person p ON p.id=m.sender_id
                             WHERE m.room_id IN ({",".join("?" * len(ids))}) AND m.kind='chat'
                             ORDER BY m.id DESC LIMIT ?""", *ids, limit)
    return [dict(r) for r in reversed(rows)]


def chat_text(conn, room_id, limit: int = 60) -> list[dict]:
    """The shared conversation, as the AI reads it: numbered, with who said it and when."""
    rows = db.all_(conn, """SELECT m.id, m.text, m.ts, p.name, p.role FROM message m JOIN person p
                            ON p.id=m.sender_id WHERE m.room_id=? AND m.kind='chat' ORDER BY m.id DESC LIMIT ?""",
                   room_id, limit)
    return [dict(r) for r in reversed(rows)]
