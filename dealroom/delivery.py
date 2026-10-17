"""Delivery: the file, its fingerprint, who downloaded it and when, and the AI check against the terms."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import db, llm, room as R, terms as T

IMAGE = (".png", ".jpg", ".jpeg", ".webp")

CHECK_PROMPT = (
    "You are the neutral witness in a deal between a freelancer and a client. These are the terms they both "
    "accepted:\n{terms}\n\nRequests the client made in the chat:\n{asks}\n\n"
    "Look at the delivered image and check each requirement. Name each requirement in 2 to 5 words (for example: \"Blue background\"). Only describe what you can actually see. "
    "Reply JSON only: {{\"seen\": one sentence describing the image, \"checks\": [{{\"requirement\": .., "
    "\"result\": \"yes\" or \"no\" or \"unclear\", \"evidence\": what in the image shows it}}]}}"
)


def save(conn, room, person, upload) -> int | None:
    if R.side(room, person) != "freelancer":
        return None
    t = T.latest(conn, room["id"])
    if not t or not t["agreed"]:
        R.note(conn, room["id"], "warn", "Agree on the terms before delivering, so the delivery can be checked against them.",
               only_for=person["id"])
        return None
    raw = upload.read()
    if not raw:
        return None
    name = re.sub(r"[^A-Za-z0-9._-]", "_", upload.filename or "file")[:80]
    sha = hashlib.sha256(raw).hexdigest()
    stored = db.UPLOADS / f"room{room['id']}-{sha[:12]}{Path(name).suffix.lower()}"
    stored.write_bytes(raw)
    did = db.insert(conn, "delivery", room_id=room["id"], filename=name, stored=str(stored), sha256=sha, ts=db.now())
    R.note(conn, room["id"], "info", f"{person['name']} delivered \"{name}\" ({len(raw) // 1024 or 1} KB, fingerprint "
                                     f"{sha[:10]}…). The client's download will be recorded.", {"delivery_id": did})
    check(conn, room, did)
    return did


def check(conn, room, did: int):
    d = db.one(conn, "SELECT * FROM delivery WHERE id=?", did)
    if not d or not d["stored"].lower().endswith(IMAGE) or not llm.available():
        return None
    t = T.latest(conn, room["id"])["data"]
    terms = json.dumps({k: t.get(k) for k in ("item", "deliverables", "revisions")})
    client_id = room["client_id"]
    asks = [m["text"] for m in R.chat_text(conn, room["id"]) if m["role"] == "client"][-6:]
    out = llm.look(CHECK_PROMPT.format(terms=terms, asks="\n".join(f"- {a}" for a in asks) or "-"), Path(d["stored"]))
    if not out or not isinstance(out.get("checks"), list):
        return None
    checks = [c for c in out["checks"] if isinstance(c, dict) and c.get("requirement") and c.get("result") in ("yes", "no", "unclear")][:6]
    db.update(conn, "delivery", did, check_data=json.dumps({"seen": out.get("seen"), "checks": checks}))
    mark = {"yes": "✓", "no": "✗", "unclear": "?"}
    line = "; ".join(f"{c['requirement']} {mark[c['result']]}" for c in checks)
    bad = [c for c in checks if c["result"] == "no"]
    level = "good" if not bad and checks else "warn"
    R.note(conn, room["id"], level, f"{llm.LABELS['vision']} checked the file against the terms: {line}. "
                                    f"It sees: {out.get('seen', '')}".strip(), {"delivery_id": did})
    return checks


def download(conn, room, person, did: int, ip: str):
    d = db.one(conn, "SELECT * FROM delivery WHERE id=? AND room_id=?", did, room["id"])
    if not d:
        return None
    first = not db.one(conn, "SELECT 1 FROM download WHERE delivery_id=? AND person_id=?", did, person["id"])
    db.insert(conn, "download", delivery_id=did, person_id=person["id"], ip=ip, ts=db.now())
    if first and person["id"] == room["client_id"]:
        R.note(conn, room["id"], "good", f"{person['name']} downloaded \"{d['filename']}\". Recorded in the room.",
               {"delivery_id": did})
    return d


def latest(conn, room_id):
    d = db.one(conn, "SELECT * FROM delivery WHERE room_id=? ORDER BY id DESC LIMIT 1", room_id)
    if not d:
        return None
    out = dict(d)
    out["check"] = db.jload(d["check_data"], {})
    out["downloads"] = [dict(x) for x in db.all_(conn, """SELECT dl.ts, p.name FROM download dl JOIN person p ON p.id=dl.person_id
                                                          WHERE dl.delivery_id=? ORDER BY dl.id""", d["id"])]
    return out
