"""DealRoom's rules, with the AI and PayPal stood in. Runs offline in a few seconds:
python -m unittest discover tests"""

import io
import json
import unittest
from unittest import mock

from dealroom import billing as B, db, delivery as D, disputes as X, llm, paypal, protect as P, room as R, terms as T, watch as WA
from dealroom.app import create_app

CHAT = [("alex", "Hi Maya! Can you draw my dog Biscuit? Blue background please"),
        ("maya", "Sure! A digital portrait is $80, with 2 revisions included. I can deliver Friday."),
        ("alex", "Perfect. Can I pay half now?"),
        ("maya", "Yes, $40 deposit now and $40 when I deliver the file.")]
GOOD_TERMS = {"item": "Portrait of Biscuit", "deliverables": ["digital portrait"], "price": 80, "deposit": 40,
              "revisions": 2, "deadline": "Friday", "quotes": {}, "missing": []}
PNG = open(db.ROOT / "samples" / "biscuit_portrait_blue.png", "rb").read()


class Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.c = self.app.test_client()
        self.c.post("/start/new")
        self.tok = self.c.get_cookie("dealroom_visitor").value
        from dealroom import workspace as W
        self.conn, _ = W.get(self.tok)
        self.room = R.get(self.conn, 1)
        self.maya, self.alex = R.person(self.conn, "maya"), R.person(self.conn, "alex")

    def say(self, who, text):
        return self.c.post("/r/1/say", data={"as": who, "text": text})

    def notes(self, who="maya", kind=None):
        ms = self.c.get(f"/r/1/feed?as={who}").json["messages"]
        return [m for m in ms if m["kind"] != "chat" and (kind is None or m["kind"] == kind)]

    def agree(self):
        for who, t in CHAT:
            self.say(who, t)
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm, "ask", return_value=dict(GOOD_TERMS)):
            self.c.post("/r/1/terms/draft", data={"as": "maya"})
        self.c.post("/r/1/terms/accept", data={"as": "maya"})
        self.c.post("/r/1/terms/accept", data={"as": "alex"})

    def pay_all(self):
        for p in B.parts(self.conn, self.room):
            db.insert(self.conn, "payment", room_id=1, part=p["part"], amount=p["amount"], order_id="T" + p["part"],
                      capture_id="C" + p["part"], status="COMPLETED", ts=db.now(), paid_ts=db.now())

    def deliver(self):
        with mock.patch.object(llm, "available", return_value=False):
            self.c.post("/r/1/deliver", data={"as": "maya", "file": (io.BytesIO(PNG), "portrait.png")},
                        content_type="multipart/form-data")


class PrivateCopies(Base):
    def test_each_visitor_has_their_own_room(self):
        self.say("maya", "hello")
        other = self.app.test_client()
        self.assertEqual(other.get("/r/1?as=maya").status_code, 302)  # no copy yet: back to the welcome page
        other.post("/start/new")
        self.assertEqual(other.get("/r/1/feed?as=maya").json["messages"], [])

    def test_strangers_cant_open_the_room(self):
        self.assertEqual(self.c.get("/r/1?as=nobody").status_code, 404)
        self.assertEqual(self.c.get("/r/1?as=sam").status_code, 403)       # another client of Maya's
        self.assertEqual(self.c.get("/r/1/feed?as=lina").status_code, 403)

    def test_story_two_is_a_finished_paid_deal(self):
        self.c.post("/start/wrong")
        self.assertTrue(all(p["paid"] for p in B.parts(self.conn, R.get(self.conn, 1))))
        self.assertTrue(D.latest(self.conn, 1)["downloads"])


class Terms(Base):
    def test_values_must_be_said_in_the_chat(self):
        chat = [{"text": t} for _, t in CHAT]
        terms, dropped = T.check(dict(GOOD_TERMS, price=95, revisions=3), chat)
        self.assertIsNone(terms["price"])        # $95 was never said
        self.assertIsNone(terms["revisions"])    # 3 revisions were never said
        self.assertEqual(set(dropped), {"price", "revisions"})

    def test_said_values_are_kept(self):
        terms, dropped = T.check(dict(GOOD_TERMS), [{"text": t} for _, t in CHAT])
        self.assertEqual((terms["price"], terms["deposit"], terms["revisions"]), (80, 40, 2))
        self.assertEqual(dropped, [])

    def test_half_means_half(self):
        chat = [{"text": "It's $120 in total"}, {"text": "Can I pay half now?"}]
        terms, _ = T.check(dict(GOOD_TERMS, price=120, deposit=60, revisions=None), chat)
        self.assertEqual(terms["deposit"], 60)

    def test_deposit_cant_exceed_price(self):
        terms, _ = T.check(dict(GOOD_TERMS, deposit=800), [{"text": "$80 total, $800"}])
        self.assertIsNone(terms["deposit"])

    def test_everything_upfront_is_one_payment(self):
        # found by tools/eval.py: "I'll pay everything upfront" came back as a deposit equal to the price
        chat = [{"text": "$150, 3 rounds"}, {"text": "I'll pay everything upfront, $150"}]
        terms, _ = T.check(dict(GOOD_TERMS, price=150, deposit=150, revisions=3), chat)
        self.assertIsNone(terms["deposit"])

    def test_both_must_accept(self):
        for who, t in CHAT:
            self.say(who, t)
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm, "ask", return_value=dict(GOOD_TERMS)):
            self.c.post("/r/1/terms/draft", data={"as": "maya"})
        self.c.post("/r/1/terms/accept", data={"as": "maya"})
        self.assertFalse(T.latest(self.conn, 1)["agreed"])
        self.c.post("/r/1/terms/accept", data={"as": "alex"})
        self.assertTrue(T.latest(self.conn, 1)["agreed"])

    def test_an_edit_needs_both_to_accept_again(self):
        self.agree()
        self.c.post("/r/1/terms/edit", data={"as": "alex", "price": "60"})
        t = T.latest(self.conn, 1)
        self.assertEqual(t["data"]["price"], 60)
        self.assertFalse(t["agreed"])


class Payments(Base):
    def test_amounts_come_only_from_the_terms(self):
        self.agree()
        self.assertEqual([(p["part"], p["amount"]) for p in B.parts(self.conn, self.room)], [("deposit", 40), ("final", 40)])

    def test_order_amount_is_locked(self):
        self.agree()
        self.c.post("/r/1/invoice", data={"as": "maya"})
        with mock.patch.object(paypal, "create_order", return_value={"id": "ORDER1"}) as co:
            r = self.c.post("/r/1/pay/deposit/order?as=alex", data={"amount": "160"})  # the browser can't choose
        self.assertEqual(r.json["id"], "ORDER1")
        self.assertEqual(co.call_args.args[0], 40)

    def test_only_the_client_pays(self):
        self.agree()
        self.assertEqual(self.c.post("/r/1/pay/deposit/order?as=maya").status_code, 403)

    def test_no_payment_before_terms(self):
        r = self.c.post("/r/1/pay/deposit/order?as=alex")
        self.assertEqual(r.status_code, 400)

    def test_capture_that_differs_from_terms_is_flagged(self):
        self.agree()
        db.insert(self.conn, "payment", room_id=1, part="deposit", amount=40, order_id="O1", status="CREATED", ts=db.now())
        res = {"purchase_units": [{"payments": {"captures": [{"id": "CAP", "status": "COMPLETED",
                                                               "amount": {"value": "160.00"}}]}}]}
        with mock.patch.object(paypal, "capture_order", return_value=res):
            B.capture(self.conn, self.room, "O1")
        self.assertIn("Don't send money back by hand", self.notes()[-1]["text"])

    def test_invoice_pdf_is_written_from_the_terms(self):
        self.agree()
        self.c.post("/r/1/invoice", data={"as": "maya"})
        r = self.c.get("/r/1/invoice.pdf?as=alex")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))


class Delivery(Base):
    def test_delivery_needs_agreed_terms(self):
        self.deliver()
        self.assertIsNone(D.latest(self.conn, 1))

    def test_download_is_recorded_once_for_the_client(self):
        self.agree()
        self.deliver()
        d = D.latest(self.conn, 1)
        self.c.get(f"/r/1/file/{d['id']}?as=alex")
        self.c.get(f"/r/1/file/{d['id']}?as=alex")
        self.assertEqual(len(D.latest(self.conn, 1)["downloads"]), 2)
        self.assertEqual(sum("downloaded" in n["text"] for n in self.notes()), 1)

    def test_ai_check_keeps_only_valid_results(self):
        self.agree()
        out = {"seen": "a dog on blue", "checks": [{"requirement": "Blue background", "result": "yes"},
                                                   {"requirement": "Invented", "result": "maybe"}]}
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm, "look", return_value=out):
            self.c.post("/r/1/deliver", data={"as": "maya", "file": (io.BytesIO(PNG), "p.png")},
                        content_type="multipart/form-data")
        self.assertEqual([c["requirement"] for c in D.latest(self.conn, 1)["check"]["checks"]], ["Blue background"])


class Protection(Base):
    def test_claim_filter_lets_complaints_through(self):
        # found by tools/eval.py: these never reached the AI
        for text in ("The background isn't blue like I asked", "I want my money back", "I never received the file"):
            self.assertTrue(P.CLAIMS.search(text), text)

    def test_off_platform_payment_warning(self):
        self.say("alex", "Can I pay you with Friends and Family instead?")
        self.assertIn("aren't protected", self.notes()[-1]["text"])

    def test_send_money_back_warning(self):
        self.say("alex", "Oops I sent too much, can you send back $40?")
        self.assertIn("Never send money back by hand", self.notes()[-1]["text"])

    def test_the_same_facts_are_never_repeated(self):
        self.agree(); self.pay_all(); self.deliver()
        self.c.get("/r/1/file/1?as=alex")
        for _ in range(2):
            self.c.post("/r/1/say", data={"as": "alex", "text": "I never received the file", "checked": "1"})
        shared = [n for n in self.notes() if n["text"].startswith("Facts from the record")]
        self.assertEqual(len(shared), 1)
        self.assertIn("Report a problem", self.notes("alex", "private")[-1]["text"])

    def test_paying_the_rest_later_is_not_a_scam(self):
        self.say("alex", "I'll send half now and the rest when it's done")
        self.assertEqual(self.notes(), [])

    def test_link_warning_is_private_to_the_other_person(self):
        self.say("alex", "pay here www.example-pay.com")
        self.assertTrue(self.notes("maya", "private"))
        self.assertFalse(self.notes("alex", "private"))

    def test_honesty_check_uses_the_record(self):
        self.agree()
        self.deliver()
        self.c.get(f"/r/1/file/{D.latest(self.conn, 1)['id']}?as=alex")
        with mock.patch.object(llm, "available", return_value=False):
            w = self.c.post("/r/1/precheck", data={"as": "alex", "text": "I never received the file"}).json["warning"]
        self.assertIn("you downloaded", w)
        self.assertEqual(self.notes("maya", "private"), [])   # never shown to the other person

    def test_no_check_when_the_record_agrees(self):
        with mock.patch.object(llm, "available", return_value=False):
            w = self.c.post("/r/1/precheck", data={"as": "alex", "text": "I never received the file"}).json["warning"]
        self.assertIsNone(w)   # nothing was delivered: the claim is fair

    def test_sent_anyway_shows_both_the_facts(self):
        self.agree()
        self.deliver()
        self.c.get(f"/r/1/file/{D.latest(self.conn, 1)['id']}?as=alex")
        with mock.patch.object(llm, "available", return_value=False):
            self.c.post("/r/1/say", data={"as": "alex", "text": "I never received it", "checked": "1"})
        self.assertIn("Facts from the record", self.notes("maya")[-1]["text"])


class Disputes(Base):
    def setUp(self):
        super().setUp()
        self.agree()
        self.pay_all()
        self.deliver()
        self.c.get(f"/r/1/file/{D.latest(self.conn, 1)['id']}?as=alex")

    def test_only_a_paid_client_can_open(self):
        self.c.post("/r/1/dispute/open", data={"as": "maya", "claim": "x", "confirm": "1"})
        self.assertIsNone(X.latest(self.conn, 1))

    def test_private_check_before_a_dispute(self):
        with mock.patch.object(llm, "available", return_value=False):
            r = self.c.post("/r/1/dispute/open", data={"as": "alex", "claim": "I never received it", "confirm": "0"})
        self.assertIn("warning", r.json)
        self.assertIsNone(X.latest(self.conn, 1))

    def test_grounding_removes_unsupported_sentences(self):
        ev = {"E1": {"what": "Alex downloaded \"portrait.png\"", "ts": "2026-10-12T14:03:00"},
              "E2": {"what": "PayPal confirmed the deposit of $40.00", "ts": "2026-10-10"}}
        items = [{"s": "Alex downloaded the file.", "e": ["E1"]},
                 {"s": "Alex paid $99.", "e": ["E2"]},                     # number not in the event
                 {"s": "He paid with Goods and Services.", "e": ["E2"]},   # names not in the event
                 {"s": "No citation here."},
                 {"s": "Cites a missing event.", "e": ["E9"]}]
        self.assertEqual(X._grounded(items, ev), "Alex downloaded the file. [E1]")

    def test_timeline_never_includes_private_notes(self):
        R.note(self.conn, 1, "info", "SECRET private hint", only_for=self.alex["id"])
        self.assertFalse(any("SECRET" in e["what"] for e in X.timeline(self.conn, self.room)))

    def test_report_and_pdf(self):
        self.c.post("/r/1/dispute/open", data={"as": "alex", "claim": "I never received it", "confirm": "1"})
        raw = {"freelancer": [{"s": "Alex downloaded the file.", "e": ["E7"]}], "client": [], "assessment": [],
               "supports": "freelancer"}
        ev = X.timeline(self.conn, self.room)
        dl = next(e["id"] for e in ev if "downloaded" in e["what"])
        raw["freelancer"][0]["e"] = [dl]
        with mock.patch.object(llm, "ask", return_value=raw):
            self.c.post("/r/1/dispute/prepare", data={"as": "maya"})
        self.assertIn(f"[{dl}]", X.latest(self.conn, 1)["report"]["freelancer"])
        self.assertTrue(self.c.get("/r/1/evidence.pdf?as=maya").data.startswith(b"%PDF"))

    def test_record_against_the_freelancer_is_said_privately(self):
        self.c.post("/r/1/dispute/open", data={"as": "alex", "claim": "x", "confirm": "1"})
        with mock.patch.object(llm, "ask", return_value={"supports": "client"}):
            self.c.post("/r/1/dispute/prepare", data={"as": "maya"})
        priv = self.notes("maya", "private")
        self.assertIn("doesn't support your side", priv[-1]["text"])
        self.assertFalse(self.notes("alex", "private"))


class AIClient(Base):
    def test_ai_client_cant_claim_an_unconfirmed_payment(self):
        from dealroom import client_ai as AI
        self.c.post("/start/ai")
        rm = R.get(self.conn, 1)
        with mock.patch.object(llm, "ask", return_value={"message": "Great, I've sent $40 via PayPal!"}):
            AI._reply(self.conn, rm)
        self.assertFalse(any("sent $40" in m["text"] for m in R.chat_text(self.conn, 1)))
        with mock.patch.object(llm, "ask", return_value={"message": "Sounds good, see you Friday!"}):
            AI._reply(self.conn, rm)
        self.assertIn("see you Friday", R.chat_text(self.conn, 1)[-1]["text"])

    def test_ai_client_is_labelled(self):
        self.c.post("/start/ai")
        self.assertEqual(R.person(self.conn, "alex")["name"], "Alex (AI)")


class Webhooks(Base):
    def test_unverified_webhooks_are_ignored(self):
        r = self.c.post("/paypal/webhook", json={"event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {}})
        self.assertEqual(r.status_code, 400)

    def test_verified_dispute_lands_in_the_right_room(self):
        self.agree()
        self.pay_all()
        B.remember_capture("CAP-1", f"{self.tok}:1")
        ev = {"event_type": "CUSTOMER.DISPUTE.CREATED", "resource": {
            "dispute_id": "PP-D-1", "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
            "disputed_transactions": [{"seller_transaction_id": "CAP-1"}]}}
        with mock.patch.object(paypal, "verify_webhook", return_value=True):
            self.c.post("/paypal/webhook", json=ev)
            self.c.post("/paypal/webhook", json=ev)   # PayPal may send it twice
        rows = db.all_(self.conn, "SELECT * FROM dispute")
        self.assertEqual([(r["paypal_id"], r["reason"]) for r in rows], [("PP-D-1", "not_received")])


class Screens(Base):
    def test_every_screen_renders(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/r/1?as=maya").status_code, 200)
        self.assertEqual(self.c.get("/r/1?as=alex").status_code, 200)
        self.agree()
        self.assertIn("Invoice and payment", self.c.get("/r/1/feed?as=alex").json["card"])



class Conversations(Base):
    """One conversation per person, like a chat app; every deal keeps its own record."""

    def test_one_conversation_per_person(self):
        chats = R.conversations(self.conn, self.maya)
        self.assertEqual(sorted(c["other"]["name"] for c in chats), ["Alex", "Lina", "Sam"])
        alex_side = R.conversations(self.conn, self.alex)
        self.assertEqual([c["other"]["name"] for c in alex_side], ["Maya"])   # Alex never sees Maya's other clients

    def test_the_conversation_shows_every_deal_but_records_stay_separate(self):
        self.agree(); self.pay_all(); self.deliver()
        mid = R.say(self.conn, 1, self.alex["id"], "Another portrait please, red background")
        WA.set_suggestion(self.conn, R.get(self.conn, 1), {"title": "Red portrait", "from": mid})
        with mock.patch.object(llm, "ask", return_value=None):
            go = self.c.post("/r/1/newdeal", data={"as": "maya"}).json["go"]
        nid = int(go.split("/r/")[1].split("?")[0])
        feed = self.c.get(f"/r/{nid}/feed?as=maya").json
        self.assertTrue(feed["multi"])
        self.assertEqual({m["deal"] for m in feed["messages"]}, {"Dog portrait", "Red portrait"})
        self.assertNotIn("Another portrait please, red background", [m["text"] for m in R.chat_text(self.conn, 1)])
        self.assertNotIn("Red portrait", [e["what"] for e in X.timeline(self.conn, R.get(self.conn, 1))])
        self.assertIn("Red portrait", feed["card"])        # the deal tabs
        self.assertEqual(len(R.conversations(self.conn, self.maya)), 3)   # still one conversation with Alex

    def test_other_clients_never_see_the_conversation(self):
        self.say("maya", "Hello Alex")
        lina = self.c.get("/r/2/feed?as=lina").json["messages"]
        self.assertNotIn("Hello Alex", [m["text"] for m in lina])


class KeepsUpWithTheChat(Base):
    """The room notices when people agree, change the deal, or ask for new work."""

    def watch(self, who, text, kind, terms=None, title=None):
        mid = R.say(self.conn, 1, R.person(self.conn, who)["id"], text)
        answers = [{"kind": kind, "title": title}] + ([dict(terms)] if terms else [])
        with mock.patch.object(llm, "available", return_value=True), mock.patch.object(llm, "ask", side_effect=answers):
            return WA.check(self.conn, R.get(self.conn, 1), mid, R.person(self.conn, who))

    def test_agreeing_in_the_chat_drafts_the_terms(self):
        for who, t in CHAT[:-1]:
            self.say(who, t)
        self.assertEqual(self.watch("maya", CHAT[-1][1], "agreed", GOOD_TERMS), "drafted")
        self.assertEqual(T.latest(self.conn, 1)["data"]["price"], 80)

    def test_a_change_before_payment_updates_the_terms(self):
        self.agree()
        red = dict(GOOD_TERMS, deliverables=["digital portrait, red background"])
        self.watch("maya", "Sure, red instead of blue!", "change_agreed", red)
        t = T.latest(self.conn, 1)
        self.assertEqual(t["version"], 2)
        self.assertFalse(t["agreed"])   # both accept again

    def test_agreeing_on_more_after_a_draft_updates_it(self):
        self.agree()
        T.edit(self.conn, 1, self.maya, {"deposit": ""})
        self.watch("maya", "Yes, half now ($40) and $40 at delivery. Deal?", "agreed", GOOD_TERMS)
        self.assertEqual(T.latest(self.conn, 1)["data"]["deposit"], 40)

    def test_a_request_alone_changes_nothing(self):
        self.agree()
        self.watch("alex", "Can it be $60 instead?", "change_asked", dict(GOOD_TERMS, price=60))
        self.watch("alex", "I was hoping for $50", "offer", dict(GOOD_TERMS, price=50))
        self.assertEqual(T.latest(self.conn, 1)["version"], 1)

    def test_no_new_version_when_nothing_changed(self):
        self.agree()
        self.watch("maya", "So $80 with 2 revisions, Friday", "change_agreed", GOOD_TERMS)
        self.assertEqual(T.latest(self.conn, 1)["version"], 1)

    def test_paid_terms_are_never_rewritten(self):
        self.agree()
        self.pay_all()
        self.watch("alex", "Can it be $60 instead?", "change_asked", dict(GOOD_TERMS, price=60))
        self.assertEqual(T.latest(self.conn, 1)["version"], 1)
        self.assertIn("under way", self.notes()[-1]["text"])

    def test_new_work_becomes_its_own_deal(self):
        self.agree()
        self.pay_all()
        self.deliver()
        self.say("alex", "Thanks, love it!")
        self.assertEqual(self.watch("alex", "Can I have another portrait, this time with a red background?", "new_work",
                                    title="Portrait, red background"), "suggested")
        self.say("maya", "Sure, same price")
        card = self.c.get("/r/1/feed?as=maya").json["card"]
        self.assertIn("Start a new deal", card)
        with mock.patch.object(llm, "ask", return_value=None):
            go = self.c.post("/r/1/newdeal", data={"as": "maya"}).json["go"]
        nid = int(go.split("/r/")[1].split("?")[0])
        new = R.get(self.conn, nid)
        self.assertEqual((new["title"], new["freelancer_id"], new["client_id"]),
                         ("Portrait, red background", self.maya["id"], self.alex["id"]))
        copied = [m["text"] for m in R.chat_text(self.conn, nid)]
        self.assertEqual(copied, ["Can I have another portrait, this time with a red background?", "Sure, same price"])
        self.assertEqual(T.latest(self.conn, 1)["version"], 1)              # the first deal stays as agreed
        self.assertNotIn("Start a new deal", self.c.get("/r/1/feed?as=maya").json["card"])
        card = self.c.get(f"/r/{nid}/feed?as=maya").json["card"]
        self.assertIn("Portrait, red background", card)
        self.assertIn("Dog portrait", card)                                   # the deal tabs list both

    def test_new_work_is_announced_once(self):
        self.agree()
        self.pay_all()
        self.watch("alex", "can i have another portrait", "new_work", title="Another portrait")
        self.watch("alex", "this time red background", "new_work", title="Portrait, red background")
        said = [n for n in self.notes() if "New work came up" in n["text"]]
        self.assertEqual(len(said), 1)
        self.assertEqual(WA.suggestion(R.get(self.conn, 1))["title"], "Portrait, red background")

    def test_same_price_can_refer_to_the_earlier_deal(self):
        self.agree()
        self.pay_all()
        self.watch("alex", "Another portrait please, red background", "new_work", title="Red portrait")
        self.say("maya", "Sure, same price as last time")
        with mock.patch.object(llm, "ask", return_value=None):
            go = self.c.post("/r/1/newdeal", data={"as": "maya"}).json["go"]
        nid = int(go.split("/r/")[1].split("?")[0])
        chat = R.chat_text(self.conn, nid)
        terms, dropped = T.check(dict(GOOD_TERMS, deadline=None), chat, T._earlier(self.conn, nid))
        self.assertEqual(terms["price"], 80)      # from the earlier deal, which is in the record
        bad, _ = T.check(dict(GOOD_TERMS, price=95, deadline=None), chat, T._earlier(self.conn, nid))
        self.assertIsNone(bad["price"])          # a number nobody said is still dropped


if __name__ == "__main__":
    unittest.main()
