"""The room: messages, notes, and the record everything else is built on."""

from __future__ import annotations

from . import db

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
        live = [r for r in rooms if r["status"] != "done"]
        open_on = max(live or rooms, key=lambda r: r["created"] or "")   # the deal that needs attention
        rows.append({"other": person_by_id(conn, other_id), "room": open_on, "deals": len(rooms),
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


def feed(conn, room_id, viewer_id, after: int = 0, chat_only: bool = False):
    """The whole conversation with this person (every deal), in the order it happened, minus others' private notes."""
    room = get(conn, room_id)
    ids = [r["id"] for r in thread(conn, room)]
    rows = db.all_(conn, f"""SELECT m.*, p.name AS sender, p.key AS sender_key, r.title AS deal FROM message m
                            LEFT JOIN person p ON p.id=m.sender_id JOIN room r ON r.id=m.room_id
                            WHERE m.room_id IN ({",".join("?" * len(ids))}) AND m.id>?
                            AND (m.only_for IS NULL OR m.only_for=?) {"AND m.kind='chat'" if chat_only else ""}
                            ORDER BY m.ts, m.id""", *ids, after, viewer_id)
    return [dict(r) | {"data": db.jload(r["data"])} for r in rows]


def notifications(conn, p, limit: int = 40) -> list[dict]:
    """DealRoom's notes for this person, from all their deals, newest first: the bell, not the chat."""
    rooms = rooms_for(conn, p)
    if not rooms:
        return []
    ids = [r["id"] for r in rooms]
    seen = (one_value(conn, "SELECT seen_note FROM person WHERE id=?", p["id"]) or 0)
    rows = db.all_(conn, f"""SELECT m.id, m.room_id, m.level, m.text, m.ts, m.kind, r.title AS deal FROM message m
                            JOIN room r ON r.id=m.room_id WHERE m.room_id IN ({",".join("?" * len(ids))})
                            AND m.kind IN ('note', 'private') AND (m.only_for IS NULL OR m.only_for=?)
                            ORDER BY m.ts DESC, m.id DESC LIMIT ?""", *ids, p["id"], limit)
    out = []
    for r in rows:
        rm = next(x for x in rooms if x["id"] == r["room_id"])
        other = rm["client"] if rm["freelancer_id"] == p["id"] else rm["freelancer"]
        out.append(dict(r) | {"with": other, "unread": r["id"] > seen, "private": r["kind"] == "private"})
    return out


def one_value(conn, sql, *args):
    row = db.one(conn, sql, *args)
    return row[0] if row else None


def mark_seen(conn, p, upto: int):
    db.update(conn, "person", p["id"], seen_note=max(upto, one_value(conn, "SELECT seen_note FROM person WHERE id=?", p["id"]) or 0))


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
