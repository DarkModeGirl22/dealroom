"""DealRoom web app (Flask)."""

from __future__ import annotations

import os
import threading

from flask import Flask, Response, abort, send_file, g, jsonify, redirect, render_template, request, url_for

from . import billing as B, client_ai as AI, db, workspace as W, delivery as D, disputes as X, llm, paypal, protect as P, room as R, terms as T, watch as WA

COOKIE = "dealroom_visitor"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("DEALROOM_SECRET", "dev-only")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

    @app.before_request
    def open_conn():
        tok = request.cookies.get(COOKIE)
        g.token, g.conn = (tok, None)
        if W.exists(tok):
            g.conn, g.lock = W.get(tok)
            g.lock.acquire()

    @app.teardown_request
    def close_conn(_exc):
        if getattr(g, "conn", None) is not None:
            try:
                g.lock.release()
            except RuntimeError:
                pass

    def viewer():
        p = R.person(g.conn, request.args.get("as") or request.form.get("as") or "maya")
        if not p:
            abort(404)
        return p

    def load(room_id):
        if g.conn is None:
            abort(redirect(url_for("home")))
        rm = R.get(g.conn, room_id)
        if not rm:
            abort(redirect(url_for("home")))
        p = viewer()
        if not R.side(rm, p):
            abort(403)
        return rm, p

    @app.get("/")
    def home():
        rm = R.get(g.conn, 1) if g.conn is not None else None
        return render_template("home.html", stories=R.STORIES, current=rm)

    @app.post("/start/<story>")
    def start(story):
        if story not in R.STORIES:
            abort(404)
        tok = g.token if W.valid(g.token) else W.new_token()
        if not W.exists(tok):
            W.cleanup()
        conn, lock = W.get(tok)
        with lock:
            rid = R.seed(conn, story)
        resp = redirect(url_for("room_page", room_id=rid, **{"as": "alex" if story == "wrong" else "maya"}))
        resp.set_cookie(COOKIE, tok, max_age=W.KEEP_SECONDS, httponly=True, samesite="Lax",
                        secure=request.is_secure)
        return resp

    @app.get("/r/<int:room_id>")
    def room_page(room_id):
        rm, p = load(room_id)
        deals = R.rooms_for(g.conn, p)
        first = rm["id"] == deals[0]["id"]   # the story's line belongs to its first deal only
        return render_template("room.html", room=rm, me=p, other=R.other(g.conn, rm, p), side=R.side(rm, p),
                               story=R.STORIES.get(rm["story"]) if first else None, paypal_client_id=paypal.client_id(),
                               deals=deals)

    @app.post("/r/<int:room_id>/say")
    def say(room_id):
        rm, p = load(room_id)
        text = (request.form.get("text") or "").strip()[:2000]
        if text:
            mid = R.say(g.conn, rm["id"], p["id"], text)
            P.scan(g.conn, rm, p, text)
            if request.form.get("checked") == "1":   # sent anyway, after the private check
                P.after_claim(g.conn, rm, p, text)
            freelancer = R.side(rm, p) == "freelancer"
            conn, lock, rid = g.conn, g.lock, rm["id"]

            def then(did):   # the AI client answers after the room has caught up with the message
                if did == "drafted":
                    AI.react(conn, lock, rid, "terms")
                elif freelancer:
                    AI.react(conn, lock, rid, "message")
            if llm.available():
                WA.after_message(conn, lock, rid, mid, p["id"], then)
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/newdeal")
    def new_deal(room_id):
        rm, p = load(room_id)
        rid = WA.start_new_deal(g.conn, rm)
        if not rid:
            return jsonify(ok=True)
        conn, lock = g.conn, g.lock

        def draft():
            with lock:
                if T.draft(conn, rid, why="New deal"):
                    AI.react(conn, lock, rid, "terms")
        if llm.available():
            threading.Thread(target=draft, daemon=True).start()
        return jsonify(go=url_for("room_page", room_id=rid, **{"as": p["key"]}))

    @app.post("/r/<int:room_id>/precheck")
    def precheck(room_id):
        rm, p = load(room_id)
        return jsonify(warning=P.precheck(g.conn, rm, p, (request.form.get("text") or "")[:2000]))

    @app.get("/r/<int:room_id>/feed")
    def feed(room_id):
        rm, p = load(room_id)
        after = request.args.get("after", type=int, default=0)
        msgs = R.feed(g.conn, rm["id"], p["id"], after)
        card = render_template("_card.html", room=rm, me=p, side=R.side(rm, p), state=state(rm))
        return jsonify(messages=msgs, card=card)

    @app.post("/r/<int:room_id>/terms/draft")
    def terms_draft(room_id):
        rm, p = load(room_id)
        if T.draft(g.conn, rm["id"]):
            AI.react(g.conn, g.lock, rm["id"], "terms")
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/terms/edit")
    def terms_edit(room_id):
        rm, p = load(room_id)
        T.edit(g.conn, rm["id"], p, request.form)
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/terms/accept")
    def terms_accept(room_id):
        rm, p = load(room_id)
        T.accept(g.conn, rm, p)
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/invoice")
    def invoice(room_id):
        rm, p = load(room_id)
        if R.side(rm, p) == "freelancer" and B.make_invoice(g.conn, rm):
            AI.react(g.conn, g.lock, rm["id"], "invoice")
        return jsonify(ok=True)

    @app.get("/r/<int:room_id>/invoice.pdf")
    def invoice_pdf(room_id):
        rm, p = load(room_id)
        pdf = B.invoice_pdf(g.conn, rm)
        if not pdf:
            abort(404)
        return Response(pdf, mimetype="application/pdf",
                        headers={"Content-Disposition": f"inline; filename=invoice-room-{rm['id']}.pdf"})

    @app.post("/r/<int:room_id>/pay/<part>/order")
    def pay_order(room_id, part):
        rm, p = load(room_id)
        if R.side(rm, p) != "client":
            abort(403)
        try:
            return jsonify(id=B.create_order(g.conn, rm, part, f"{g.token}:{rm['id']}"))
        except (ValueError, paypal.PayPalError) as e:
            return jsonify(error=str(e)[:200]), 400

    @app.post("/r/<int:room_id>/pay/capture")
    def pay_capture(room_id):
        rm, p = load(room_id)
        try:
            row = B.capture(g.conn, rm, (request.get_json(silent=True) or request.form).get("order_id", ""),
                            f"{g.token}:{rm['id']}")
            return jsonify(status=row["status"])
        except (ValueError, KeyError, paypal.PayPalError) as e:
            return jsonify(error=str(e)[:200]), 400

    @app.post("/r/<int:room_id>/deliver")
    def deliver(room_id):
        rm, p = load(room_id)
        f = request.files.get("file")
        if f and D.save(g.conn, rm, p, f):
            AI.react(g.conn, g.lock, rm["id"], "delivery")
        return jsonify(ok=True)

    @app.get("/r/<int:room_id>/file/<int:did>")
    def get_file(room_id, did):
        rm, p = load(room_id)
        d = D.download(g.conn, rm, p, did, request.headers.get("X-Forwarded-For", request.remote_addr or ""))
        if not d:
            abort(404)
        return send_file(d["stored"], download_name=d["filename"], as_attachment=False)

    @app.post("/r/<int:room_id>/dispute/open")
    def dispute_open(room_id):
        rm, p = load(room_id)
        claim = (request.form.get("claim") or "").strip()[:500]
        if request.form.get("confirm") != "1":
            w = P.precheck(g.conn, rm, p, claim or "I never received it")
            if w:
                return jsonify(warning=w)
        X.open_dispute(g.conn, rm, p, request.form.get("reason", "not_received"), claim)
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/dispute/prepare")
    def dispute_prepare(room_id):
        rm, p = load(room_id)
        X.prepare(g.conn, rm, p)
        return jsonify(ok=True)

    @app.post("/r/<int:room_id>/dispute/send")
    def dispute_send(room_id):
        rm, p = load(room_id)
        X.send(g.conn, rm, p)
        return jsonify(ok=True)

    @app.get("/r/<int:room_id>/evidence.pdf")
    def evidence_pdf(room_id):
        rm, p = load(room_id)
        pdf = X.report_pdf(g.conn, rm)
        if not pdf:
            abort(404)
        return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": "inline; filename=evidence.pdf"})

    @app.post("/paypal/webhook")
    def paypal_webhook():
        """PayPal tells DealRoom about payments, refunds and disputes. Only verified events are used."""
        ev = request.get_json(silent=True) or {}
        if not paypal.verify_webhook(request.headers, ev):
            return jsonify(ok=False), 400
        res, kind = ev.get("resource") or {}, ev.get("event_type", "")
        if kind.startswith("PAYMENT.CAPTURE."):
            custom, capture_id = res.get("custom_id"), res.get("id")
        elif kind.startswith("CUSTOMER.DISPUTE."):
            tx = (res.get("disputed_transactions") or [{}])[0]
            capture_id = tx.get("seller_transaction_id")
            custom = tx.get("custom") or B.lookup_capture(capture_id or "")
        else:
            return jsonify(ok=True)
        if not custom or ":" not in custom:
            custom = B.lookup_capture(capture_id or "")
        if not custom or ":" not in custom:
            return jsonify(ok=True)
        tok, room_id = custom.split(":", 1)
        if not W.exists(tok):
            return jsonify(ok=True)
        conn, lock = W.get(tok)
        with lock:
            rm = R.get(conn, int(room_id))
            if rm:
                X.from_paypal(conn, rm, kind, res, capture_id)
        return jsonify(ok=True)

    def state(rm):
        """Everything the deal card shows, read from the record."""
        t = T.latest(g.conn, rm["id"])
        return {"terms": t, "parts": B.parts(g.conn, rm), "ai": llm.available(),
                "paypal": paypal.configured(), "client_id": paypal.client_id(),
                "delivery": D.latest(g.conn, rm["id"]), "dispute": X.latest(g.conn, rm["id"]),
                "new_work": WA.suggestion(R.get(g.conn, rm["id"]))}

    return app
