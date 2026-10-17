"""Tricky scenarios, run against the real app with the real NVIDIA Nemotron models.

    NEBIUS_API_KEY=... python tools/tricky.py

Unit tests check the rules with the AI stood in. This checks what real people do: typos, haggling,
changing their mind, lying, writing in French, scams in other words, the wrong file. Each scenario
says what must happen; the run prints PASS or FAIL for each.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEALROOM_DATA_DIR"] = tempfile.mkdtemp(prefix="dealroom-tricky-")

from dealroom import billing as B, db, llm, room as R, terms as T, watch as WA, workspace as W  # noqa: E402
from dealroom.app import create_app  # noqa: E402

RESULTS = []


class Run:
    def __init__(self, story="new"):
        self.c = create_app().test_client()
        self.c.post(f"/start/{story}")
        self.conn, self.lock = W.get(self.c.get_cookie("dealroom_visitor").value)
        self.room = 1

    def count(self):
        with self.lock:
            return db.one(self.conn, "SELECT COUNT(*) n, MAX(id) m FROM message")["m"] or 0

    def quiet(self, settle=5, most=60):
        """Wait until the background AI (the room watcher, the AI client) has finished."""
        t0, last, since = time.time(), self.count(), time.time()
        while time.time() - t0 < most:
            time.sleep(1)
            now = self.count()
            if now != last:
                last, since = now, time.time()
            elif time.time() - since >= settle:
                break

    def say(self, who, text, anyway=False, wait=True):
        warn = self.c.post(f"/r/{self.room}/precheck", data={"as": who, "text": text}).json["warning"]
        self.c.post(f"/r/{self.room}/say", data={"as": who, "text": text, "checked": "1" if (warn or anyway) else "0"})
        if wait:
            self.quiet()
        return warn

    def notes(self, who="maya", since=0):
        ms = self.c.get(f"/r/{self.room}/feed?as={who}&after={since}").json["messages"]
        return [m["text"] for m in ms if m["kind"] != "chat"]

    def terms(self):
        with self.lock:
            return T.latest(self.conn, self.room)

    def accept_both(self):
        for who in ("maya", "alex"):
            self.c.post(f"/r/{self.room}/terms/accept", data={"as": who})

    def pay_all(self):
        with self.lock:
            rm = R.get(self.conn, self.room)
            for p in B.parts(self.conn, rm):
                if not p["paid"]:
                    db.insert(self.conn, "payment", room_id=self.room, part=p["part"], amount=p["amount"], order_id="T",
                              capture_id="C" + p["part"], status="COMPLETED", ts=db.now(), paid_ts=db.now())

    def deliver(self, name):
        data = (ROOT / "samples" / name).read_bytes()
        self.c.post(f"/r/{self.room}/deliver", data={"as": "maya", "file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data")
        self.quiet()

    def download(self, who="alex"):
        with self.lock:
            d = db.one(self.conn, "SELECT id FROM delivery WHERE room_id=? ORDER BY id DESC", self.room)
        self.c.get(f"/r/{self.room}/file/{d['id']}?as={who}")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


def any_note(r, *words, who="maya", since=0):
    return any(all(w.lower() in n.lower() for w in words) for n in r.notes(who, since))


def deal(r, lines):
    for who, text in lines:
        r.say(who, text)


def s_typos():
    r = Run()
    deal(r, [("alex", "hey can u draw my cat?? pink bg pls"), ("maya", "yes 50$ , 2 revisons, by tuesday"),
             ("alex", "ok deal")])
    t = r.terms()
    d = t["data"] if t else {}
    check("typos: terms drafted on their own", t is not None)
    check("typos: price 50, 2 revisions, Tuesday",
          d.get("price") == 50 and d.get("revisions") == 2 and "tue" in (d.get("deadline") or "").lower(), str(d))


def s_haggling():
    r = Run()
    deal(r, [("alex", "How much for a portrait of my dog?"), ("maya", "$80, ready Friday"),
             ("alex", "too much, can you do 60?"), ("maya", "ok 70, final offer"), ("alex", "deal, 70 it is")])
    d = (r.terms() or {}).get("data") or {}
    check("haggling: the last agreed price wins (70, not 80 or 60)", d.get("price") == 70, str(d.get("price")))


def s_mind_change_before_payment():
    r = Run()
    deal(r, [("alex", "Portrait of my dog Biscuit, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    v1 = r.terms()
    r.say("alex", "actually, can you make the background green instead?")
    check("change asked but not yet accepted: terms unchanged", r.terms()["version"] == v1["version"])
    r.say("maya", "sure, green it is!")
    t = r.terms()
    check("change before payment: new version of the terms", v1 and t and t["version"] > v1["version"],
          f"v{v1 and v1['version']} -> v{t and t['version']}")
    check("change before payment: both must accept again", t and not t["agreed"])
    check("change before payment: green is in the new terms",
          t and "green" in " ".join(t["data"].get("deliverables") or []).lower(), str(t and t["data"].get("deliverables")))


def s_mind_change_after_payment():
    r = Run()
    deal(r, [("alex", "Portrait of my dog, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    r.c.post("/r/1/invoice", data={"as": "maya"})
    r.pay_all()
    v = r.terms()["version"]
    since = r.count()
    r.say("alex", "Can we lower it to $60?")
    check("change after payment: paid terms never rewritten", r.terms()["version"] == v)
    check("change after payment: told it stays as agreed", any_note(r, "under way", since=since))


def s_false_paid_claim():
    r = Run()
    deal(r, [("alex", "Logo for my bakery please"), ("maya", "$100, 2 revisions, Monday. Full payment upfront."),
             ("alex", "perfect, deal")])
    r.accept_both()
    warn = r.say("alex", "I already sent you the money yesterday", wait=False)
    check("client says he paid, nothing paid: private warning", warn and "hasn't confirmed" in warn, str(warn)[:90])
    r.quiet()
    check("...and the freelancer sees the same fact after he sends it anyway", any_note(r, "hasn't confirmed", who="maya"))


def s_false_delivered_claim():
    r = Run()
    deal(r, [("alex", "Logo for my bakery please"), ("maya", "$100, 2 revisions, Monday. Full payment upfront."),
             ("alex", "perfect, deal")])
    r.accept_both()
    warn = r.say("maya", "I sent you the logo already, check your email", wait=False)
    check("freelancer says she delivered, nothing delivered: private warning", warn and "no file" in warn, str(warn)[:90])


def s_promise_is_not_a_claim():
    r = Run()
    deal(r, [("alex", "Logo for my bakery please"), ("maya", "$100, 2 revisions, Monday. Full payment upfront."),
             ("alex", "perfect, deal")])
    r.accept_both()
    warn = r.say("alex", "Great, I'll pay right now with the button", wait=False)
    check("'I'll pay now' is a promise, not a lie: no warning", not warn, str(warn))


def s_french_lie():
    r = Run()
    deal(r, [("alex", "Portrait of my dog, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    r.c.post("/r/1/invoice", data={"as": "maya"})
    r.pay_all()
    r.deliver("biscuit_portrait_blue.png")
    r.download()
    warn = r.say("alex", "je n'ai jamais reçu le fichier", wait=False)
    check("French: 'never received' after downloading gets the private check", warn and "downloaded" in warn, str(warn)[:90])


def s_scam_words():
    cases = ["can you pay me through Wise instead?", "send it to my revolut pls", "I prefer a bank transfer to my IBAN",
             "let's continue on whatsapp, easier", "pay me in USDT", "paiement en amis et famille stp"]
    for text in cases:
        r = Run()
        since = r.count()
        r.say("maya", text)
        check(f"scam wording caught: \"{text}\"", any_note(r, "outside", who="alex", since=since))


def s_fake_link():
    r = Run()
    since = r.count()
    r.say("maya", "pay here: paypal-secure-verify.com/login")
    check("link without http is still flagged for the other person", any_note(r, "link", who="alex", since=since))


def s_innocent_messages():
    r = Run()
    deal(r, [("alex", "Portrait of my dog, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    v = r.terms()["version"]
    since = r.count()
    for text in ["lol ok", "thanks!!", "what time is it for you?", "I'll sue you", "send me the invoice pls"]:
        r.say("alex", text)
    check("small talk and threats change nothing", r.terms()["version"] == v)
    check("small talk and threats: no new-deal offer", not any_note(r, "New work", since=since))


def s_new_work_in_french():
    r = Run()
    deal(r, [("alex", "Portrait of my dog, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    r.c.post("/r/1/invoice", data={"as": "maya"})
    r.pay_all()
    since = r.count()
    r.say("alex", "tu peux me faire un autre portrait, de mon chat cette fois ?")
    check("new work asked in French: new deal offered", any_note(r, "New work", since=since))


def s_wrong_file():
    r = Run()
    deal(r, [("alex", "Portrait of my dog Biscuit, blue background please"), ("maya", "$80, 2 revisions, Friday. Half now."),
             ("alex", "ok deal")])
    r.accept_both()
    r.c.post("/r/1/invoice", data={"as": "maya"})
    r.pay_all()
    since = r.count()
    r.deliver("biscuit_portrait_red.png")
    check("red file for a blue deal: the check says no", any_note(r, "Blue background ✗", since=since)
          or any_note(r, "background ✗", since=since), " | ".join(r.notes(since=since))[:160])


def s_wrong_pdf():
    r = Run()
    deal(r, [("alex", "Can you translate my CV from French to English? 2 pages"), ("maya", "Yes, $40, 1 revision, 3 days."),
             ("alex", "great, thanks!")])
    r.accept_both()
    r.c.post("/r/1/invoice", data={"as": "maya"})
    r.pay_all()
    since = r.count()
    r.deliver("cv_lina_french.pdf")
    check("untranslated PDF: the check says no", any_note(r, "English ✗", since=since), " | ".join(r.notes(since=since))[:160])


def s_deliver_before_terms():
    r = Run()
    r.say("alex", "draw my dog pls")
    r.deliver("biscuit_portrait_blue.png")
    with r.lock:
        n = db.one(r.conn, "SELECT COUNT(*) n FROM delivery WHERE room_id=1")["n"]
    check("no delivery before both accept the terms", n == 0)


def s_ai_client_overpriced():
    r = Run("ai")
    r.say("maya", "Hi Alex! It's $500, ready Friday.")
    with r.lock:
        rows = db.all_(r.conn, "SELECT data, accepted_client FROM terms WHERE room_id=1")
    bad = [db.jload(x["data"])["price"] for x in rows if x["accepted_client"] and (db.jload(x["data"]).get("price") or 0) > 90]
    check("AI client never accepts an overpriced deal", not bad, str(bad))
    t = r.terms()
    check("a quote and a counter-offer don't rewrite the terms", (t["data"]["price"] if t else None) in (None, 500.0),
          str(t and t["data"]["price"]))


def s_ai_client_never_claims_payment():
    r = Run("ai")
    deal(r, [("maya", "Hi Alex! Sure, $80 with 2 revisions, ready Friday. $40 now and $40 at delivery."),
             ("maya", "Did you pay yet?"), ("maya", "Please send the deposit now")])
    with r.lock:
        texts = [m["text"] for m in R.chat_text(r.conn, 1) if m["role"] == "client"]
    import re
    bad = [t for t in texts if re.search(r"\b(?:i'?ve|i have|i just|just)\s+(?:sent|paid|transferred)", t, re.I)]
    check("AI client never says it paid when PayPal shows nothing", not bad, " | ".join(bad)[:120])


SCENARIOS = [s_typos, s_haggling, s_mind_change_before_payment, s_mind_change_after_payment, s_false_paid_claim,
             s_false_delivered_claim, s_promise_is_not_a_claim, s_french_lie, s_scam_words, s_fake_link,
             s_innocent_messages, s_new_work_in_french, s_wrong_file, s_wrong_pdf, s_deliver_before_terms,
             s_ai_client_overpriced, s_ai_client_never_claims_payment]


def main():
    if not llm.key():
        sys.exit("Set NEBIUS_API_KEY first.")
    only = sys.argv[1:]
    c0 = llm.usage()
    for s in SCENARIOS:
        if only and s.__name__ not in only:
            continue
        try:
            s()
        except Exception as e:   # a crash is a failure too
            check(s.__name__ + " crashed", False, repr(e)[:160])
    c1 = llm.usage()
    ok = sum(1 for _, k, _ in RESULTS if k)
    print(f"\n{ok} of {len(RESULTS)} checks passed. {c1['calls'] - c0['calls']} model calls, "
          f"${c1['total_usd'] - c0['total_usd']:.3f}.")


if __name__ == "__main__":
    main()
