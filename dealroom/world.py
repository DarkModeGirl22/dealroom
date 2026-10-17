"""The demo world each visitor gets: two accounts, each in the middle of their week.

Maya (freelancer) has an Alex who owes her the last $40, a Jordan who opened a PayPal dispute after
downloading his file, Sam (played by Nemotron) asking for a logo, and a history of finished deals.
Alex (client) has Maya waiting for his payment, a translator whose PDF is still in French, and
Rafael (played by Nemotron) ready to design a thumbnail.

Past payments are recorded samples, marked "(sample)": a real PayPal payment needs a person to
approve it. Everything a visitor does from here (payments, AI, disputes) is real.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone

from . import db

PEOPLE = [
    # key, name, role, email
    ("maya", "Maya", "freelancer", "maya.sample@example.com"),
    ("alex", "Alex", "client", "alex.sample@example.com"),
    ("jordan", "Jordan", "client", "jordan.sample@example.com"),
    ("lina", "Lina", "client", "lina.sample@example.com"),
    ("sam", "Sam (AI)", "client", "sam.sample@example.com"),
    ("priya", "Priya", "client", "priya.sample@example.com"),
    ("tom", "Tom", "client", "tom.sample@example.com"),
    ("chloe", "Chloé", "client", "chloe.sample@example.com"),
    ("nora", "Nora", "freelancer", "nora.sample@example.com"),
    ("rafael", "Rafael (AI)", "freelancer", "rafael.sample@example.com"),
]
LOGINS = ("maya", "alex")


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


class Builder:
    def __init__(self, conn):
        self.conn = conn
        self.ids = {}
        self.feed = []   # (hours ago, room, sender key or None, kind, level, text, data)

    def people(self):
        for k, n, r, e in PEOPLE:
            self.ids[k] = db.insert(self.conn, "person", key=k, name=n, role=r, email=e)

    def room(self, title, freelancer, client, hours, status="talking"):
        return db.insert(self.conn, "room", title=title, freelancer_id=self.ids[freelancer], client_id=self.ids[client],
                         status=status, story="world", created=ago(hours))

    def chat(self, rid, lines):
        for h, who, text in lines:
            self.feed.append((h, rid, who, "chat", None, text, None))

    def note(self, rid, h, level, text, only_for=None):
        self.feed.append((h, rid, None, "note", level, text, {"only_for": only_for} if only_for else None))

    def terms(self, rid, h, data, invoice):
        data = {"refund_policy": None, "quotes": {}, **data}
        db.insert(self.conn, "terms", room_id=rid, version=1, data=json.dumps(data), source="ai",
                  accepted_freelancer=ago(h - 0.05), accepted_client=ago(h - 0.1), invoice_number=invoice, ts=ago(h))
        self.note(rid, h - 0.1, "good", "Both accepted the terms (version 1). They're now part of the record.")

    def paid(self, rid, h, part, amount, who):
        db.insert(self.conn, "payment", room_id=rid, part=part, amount=amount, order_id=f"SAMPLE-{rid}-{part}",
                  capture_id=f"SAMPLE-{rid}-{part}", status="COMPLETED", payer_email=f"{who}.sample@example.com",
                  ts=ago(h + 0.05), paid_ts=ago(h))
        label = {"deposit": "Deposit", "final": "Final payment", "full": "Payment"}[part]
        self.note(rid, h, "good", f"{label} of ${amount:,.2f} received through PayPal. It matches the terms exactly. (sample)")

    def delivered(self, rid, h, sender, sample, filename, check, downloader=None, dh=None):
        src = db.ROOT / "samples" / sample
        raw = src.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        stored = db.UPLOADS / f"sample-{sha[:12]}{src.suffix}"
        if not stored.exists():
            shutil.copy(src, stored)
        did = db.insert(self.conn, "delivery", room_id=rid, filename=filename, stored=str(stored), sha256=sha,
                        check_data=json.dumps(check), ts=ago(h))
        self.feed.append((h, rid, sender, "chat", None, f"📎 {filename}", {"delivery_id": did}))
        marks = "; ".join(f"{c['requirement']} {'✓' if c['result'] == 'yes' else '✗' if c['result'] == 'no' else '?'}"
                          for c in check["checks"])
        bad = any(c["result"] == "no" for c in check["checks"])
        model = "NVIDIA Nemotron 3 Super" if filename.endswith(".pdf") else "NVIDIA Nemotron 3.5 Super VL"
        self.note(rid, h - 0.01, "warn" if bad else "good", f"{model} checked the file against the terms: {marks}. "
                  f"It sees: {check['seen']}")
        if downloader:
            db.insert(self.conn, "download", delivery_id=did, person_id=self.ids[downloader], ip="sample", ts=ago(dh))
            name = dict((k, n) for k, n, *_ in PEOPLE)[downloader]
            self.note(rid, dh, "good", f"{name} downloaded \"{filename}\". Recorded in the room.")
        return did

    def flush(self):
        for h, rid, who, kind, level, text, data in sorted(self.feed, key=lambda x: -x[0]):
            only = (data or {}).pop("only_for", None) if data else None
            db.insert(self.conn, "message", room_id=rid, sender_id=self.ids[who] if who else None, kind=kind,
                      level=level, only_for=self.ids[only] if only else None, text=text,
                      data=json.dumps(data) if data else None, ts=ago(h))


CHECK = lambda seen, *checks: {"seen": seen, "checks": [{"requirement": r, "result": v, "evidence": e} for r, v, e in checks]}


def seed(conn, bare: bool = False):
    """Start (or restart) the demo world in this visitor's private copy. `bare` (for tests): the people and
    three empty conversations, Maya with Alex (1), Lina (2) and Sam (3)."""
    for t in ("download", "delivery", "payment", "terms", "message", "dispute", "room", "person"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    w = Builder(conn)
    w.people()
    if bare:
        for title, client in (("Dog portrait", "alex"), ("CV translation", "lina"), ("Bakery logo", "sam")):
            w.room(title, "maya", client, 0)
        return w.ids

    # --- Maya and Alex: the portrait is delivered and loved; the last $40 is due (paid for real by the visitor)
    r = w.room("Dog portrait", "maya", "alex", 50, "agreed")
    w.chat(r, [(50, "alex", "Hi Maya! I love your pet portraits. Could you draw my dog Biscuit on a blue background?"),
               (49.8, "maya", "Of course! $80 with 2 revisions, ready Friday. $40 now and $40 at delivery."),
               (49.6, "alex", "Perfect, deal!")])
    w.terms(r, 49.5, {"item": "Digital portrait of Biscuit", "deliverables": ["digital portrait of Biscuit", "blue background"],
                      "price": 80.0, "deposit": 40.0, "revisions": 2, "deadline": "Friday"}, "DR-0001-1")
    w.paid(r, 49, "deposit", 40.0, "alex")
    w.delivered(r, 3, "maya", "biscuit_portrait_blue.png", "biscuit_portrait.png",
                CHECK("A digital portrait of a light brown dog with floppy ears and a red collar, on a solid blue background.",
                      ("Portrait of Biscuit", "yes", "a light brown dog is the subject"),
                      ("Blue background", "yes", "solid blue background")), "alex", 2.6)
    w.chat(r, [(2.5, "alex", "I love it, Biscuit looks amazing! 😍"),
               (2.4, "maya", "Thank you! When you have a moment, could you pay the remaining $40 on the deal card?")])

    # --- Maya and Jordan: paid, delivered, downloaded... then a PayPal dispute saying he never received it
    r = w.room("Podcast cover", "maya", "jordan", 120, "agreed")
    w.chat(r, [(120, "jordan", "Hey Maya, I need cover art for my podcast Night Shift Stories. Dark blue, with a moon."),
               (119.7, "maya", "Love it. $120, 1 revision, ready in 4 days, paid upfront."),
               (119.5, "jordan", "Deal.")])
    w.terms(r, 119.4, {"item": "Podcast cover art", "deliverables": ["podcast cover", "dark blue", "a moon",
                                                                       "title Night Shift Stories"],
                       "price": 120.0, "deposit": None, "revisions": 1, "deadline": "4 days"}, "DR-0002-1")
    w.paid(r, 119, "full", 120.0, "jordan")
    w.delivered(r, 30, "maya", "podcast_cover.png", "night_shift_cover.png",
                CHECK("A dark blue night sky with stars, a pale yellow crescent moon and the title NIGHT SHIFT STORIES.",
                      ("Dark blue", "yes", "dark blue background"), ("A moon", "yes", "crescent moon, top right"),
                      ("Title Night Shift Stories", "yes", "the title is written at the bottom")), "jordan", 29.5)
    w.chat(r, [(29.4, "jordan", "got it thx")])
    db.insert(conn, "dispute", room_id=r, paypal_id=None, reason="not_received", claim="I never received the file",
              status="open", ts=ago(5))
    w.note(r, 5, "bad", "PayPal reported a dispute on this payment: Item not received, \"I never received the file\". "
                        "PayPal decides; the room's record is ready for it. (sample)")

    # --- Maya and Sam (played by Nemotron): a new request, no price yet
    r = w.room("Bakery logo", "maya", "sam", 1)
    w.chat(r, [(1, "sam", "Hi! I'm opening a small bakery called Golden Crumb. Could you design a logo? Something warm, "
                          "with a croissant."),
               (0.9, "maya", "I'd love to! Any colors in mind?"),
               (0.8, "sam", "Brown and cream. What would it cost?")])

    # --- Maya and Lina: a finished translation, delivered as a PDF
    r = w.room("CV translation", "maya", "lina", 200, "done")
    w.chat(r, [(200, "lina", "Hello Maya, could you translate my CV from French to English? It's 2 pages."),
               (199.5, "maya", "Of course: $40, 1 revision, ready in 3 days."),
               (199, "lina", "Perfect, thank you!")])
    w.terms(r, 198.9, {"item": "CV translation", "deliverables": ["CV translated from French to English", "2 pages"],
                       "price": 40.0, "deposit": None, "revisions": 1, "deadline": "3 days"}, "DR-0004-1")
    w.paid(r, 198.4, "full", 40.0, "lina")
    w.delivered(r, 150, "maya", "cv_lina_english.pdf", "CV_Lina_Haddad_EN.pdf",
                CHECK("A two-page CV for Lina Haddad, in English.",
                      ("Translated to English", "yes", "\"Marketing assistant with three years of experience\""),
                      ("2 pages", "yes", "the PDF has 2 pages")), "lina", 149)
    w.chat(r, [(148, "lina", "Got it, it's perfect. Thanks!")])

    # --- Maya's history: finished, paid deals over the last months (for her dashboard)
    for client, title, price, days, sample in [("priya", "Wedding invitation", 90.0, 150, "biscuit_portrait_blue.png"),
                                               ("jordan", "Podcast logo", 60.0, 140, "podcast_cover.png"),
                                               ("tom", "Band poster", 150.0, 105, "podcast_cover.png"),
                                               ("lina", "LinkedIn banner", 35.0, 75, "youtube_thumbnail.png"),
                                               ("chloe", "Cat portrait", 80.0, 40, "biscuit_portrait_red.png"),
                                               ("tom", "Album cover", 180.0, 20, "podcast_cover.png")]:
        h = days * 24
        r = w.room(title, "maya", client, h, "done")
        w.chat(r, [(h, client, f"Hi Maya, could you do a {title.lower()} for me?"),
                   (h - 0.5, "maya", f"Sure! ${price:g}, 1 revision, paid upfront."), (h - 1, client, "Deal!")])
        w.terms(r, h - 1.1, {"item": title, "deliverables": [title.lower()], "price": price, "deposit": None,
                             "revisions": 1, "deadline": None}, f"DR-{r:04d}-1")
        w.paid(r, h - 2, "full", price, client)
        w.delivered(r, h - 70, "maya", sample, f"{title.lower().replace(' ', '_')}.png",
                    CHECK(f"The {title.lower()}.", (title, "yes", "matches the request")), client, h - 71)
        w.chat(r, [(h - 72, client, "Perfect, thank you!")])
        if title in ("Band poster", "Wedding invitation"):   # scam attempts the room flagged at the time
            ask = ("Could I pay you through Friends & Family? It's cheaper for both of us." if title == "Band poster"
                   else "Can we continue on WhatsApp? Easier for me.")
            w.chat(r, [(h - 0.8, client, ask), (h - 0.9, "maya", "I'd rather keep everything here, thanks!")])
            w.note(r, h - 0.81, "warn", "Payments and messages outside this room aren't protected or recorded. Pay only "
                                        "with the PayPal button on the deal card; it's the only payment DealRoom can prove.")

    # --- Alex and Nora: paid, downloaded, and the "translation" is still in French
    r = w.room("About page translation", "nora", "alex", 72, "agreed")
    w.chat(r, [(72, "alex", "Hi Nora, could you translate my shop's About page from French to English? It's one page."),
               (71.8, "nora", "Sure: $60, 1 revision, ready in 2 days. Full payment upfront. You can pay me on Wise if you prefer, no fees."),
               (71.7, "alex", "I'll use the PayPal button here, thanks."),
               (71.6, "alex", "Deal!")])
    w.note(r, 71.79, "warn", "Payments and messages outside this room aren't protected or recorded. Pay only with the "
                             "PayPal button on the deal card; it's the only payment DealRoom can prove.")
    w.terms(r, 71.5, {"item": "About page translation", "deliverables": ["About page translated from French to English",
                                                                         "one page"],
                      "price": 60.0, "deposit": None, "revisions": 1, "deadline": "2 days"}, "DR-0003-1")
    w.paid(r, 71, "full", 60.0, "alex")
    w.delivered(r, 20, "nora", "about_page_french.pdf", "About_Biscuit_and_Co_EN.pdf",
                CHECK("A one-page About page for Biscuit & Co, written in French.",
                      ("Translated to English", "no", "\"Biscuit & Co fabrique des friandises naturelles pour chiens\""),
                      ("One page", "yes", "the PDF has 1 page")), "alex", 19)
    w.chat(r, [(18.8, "alex", "Hi Nora, I opened the file but it's still in French?")])

    # --- Alex and Rafael (played by Nemotron): a thumbnail, just started
    r = w.room("YouTube thumbnail", "rafael", "alex", 0.5)
    w.chat(r, [(0.5, "alex", "Hi Rafael! Could you design a thumbnail for my YouTube video about training my dog?"),
               (0.45, "rafael", "Happy to! Any colors or text you want on it?")])

    w.flush()
    # what each account has already seen: older notes are read, the last day's are new in the bell
    for key, hours in (("maya", 6), ("alex", 21)):
        row = db.one(conn, "SELECT MAX(id) m FROM message WHERE kind!='chat' AND ts<?", ago(hours))
        db.update(conn, "person", w.ids[key], seen_note=row["m"] or 0)
    return w.ids


def first_room(conn, key: str) -> int | None:
    """The conversation each account opens on: the one that needs them now."""
    title = {"maya": "Dog portrait", "alex": "Dog portrait"}.get(key)
    row = db.one(conn, "SELECT id FROM room WHERE title=? ORDER BY id LIMIT 1", title)
    return row["id"] if row else None
