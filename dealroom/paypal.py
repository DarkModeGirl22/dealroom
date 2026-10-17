"""PayPal REST API (sandbox by default): orders with a locked amount, invoices, refunds, disputes."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("PAYPAL_API", "https://api-m.sandbox.paypal.com").rstrip("/")
_token = {"value": None, "exp": 0.0}


def client_id() -> str:
    return os.environ.get("PAYPAL_CLIENT_ID", "").strip()


def configured() -> bool:
    return bool(client_id() and os.environ.get("PAYPAL_SECRET", "").strip())


class PayPalError(Exception):
    pass


def _token_value() -> str:
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    auth = base64.b64encode(f"{client_id()}:{os.environ.get('PAYPAL_SECRET', '').strip()}".encode()).decode()
    req = urllib.request.Request(f"{BASE}/v1/oauth2/token", data=b"grant_type=client_credentials",
                                 headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    _token.update(value=d["access_token"], exp=time.time() + d.get("expires_in", 3000))
    return _token["value"]


def call(method: str, path: str, body=None, headers=None, raw: bytes | None = None, ctype="application/json"):
    if not configured():
        raise PayPalError("PayPal keys are not set")
    h = {"Authorization": f"Bearer {_token_value()}", "Content-Type": ctype,
         "PayPal-Request-Id": str(uuid.uuid4())}
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read()
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise PayPalError(f"{e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PayPalError(f"PayPal unreachable: {e}") from None


def money(v: float, cur: str = "USD") -> dict:
    return {"currency_code": cur, "value": f"{v:.2f}"}


def create_order(amount: float, description: str, reference: str, invoice_number: str | None = None,
                 custom_id: str | None = None) -> dict:
    """The amount is set here, by DealRoom, from the accepted terms. The client can't change it."""
    unit = {"reference_id": reference[:250], "description": description[:127], "amount": money(amount)}
    if custom_id:
        unit["custom_id"] = custom_id[:127]
    if invoice_number:
        unit["invoice_id"] = invoice_number[:127]
    return call("POST", "/v2/checkout/orders", {"intent": "CAPTURE", "purchase_units": [unit],
                                                "payment_source": {"paypal": {"experience_context": {
                                                    "shipping_preference": "NO_SHIPPING",
                                                    "user_action": "PAY_NOW"}}}})


def capture_order(order_id: str) -> dict:
    return call("POST", f"/v2/checkout/orders/{order_id}/capture", {})


def get_order(order_id: str) -> dict:
    return call("GET", f"/v2/checkout/orders/{order_id}")


def refund(capture_id: str, amount: float | None = None, note: str = "") -> dict:
    body = {"note_to_payer": note[:255]} if note else {}
    if amount is not None:
        body["amount"] = money(amount)
    return call("POST", f"/v2/payments/captures/{capture_id}/refund", body)


def create_invoice(terms: dict, freelancer_email: str, client_email: str, number: str) -> dict:
    """A PayPal invoice written from the accepted terms: no tips, deposit as the minimum first payment."""
    price, deposit = float(terms["price"]), float(terms.get("deposit") or 0)
    lines = [f"Agreed in DealRoom: {terms.get('item', 'Work')}."]
    if terms.get("revisions") is not None:
        lines.append(f"{terms['revisions']} revisions included.")
    if terms.get("deadline"):
        lines.append(f"Delivery: {terms['deadline']}.")
    cfg = {"allow_tip": False, "partial_payment": {"allow_partial_payment": bool(deposit)}}
    if deposit:
        cfg["partial_payment"]["minimum_amount_due"] = money(deposit)
    body = {"detail": {"invoice_number": number, "currency_code": "USD", "note": " ".join(lines)[:4000],
                       "terms_and_conditions": (terms.get("refund_policy") or "As agreed in the DealRoom record.")[:4000]},
            "primary_recipients": [{"billing_info": {"email_address": client_email}}],
            "items": [{"name": terms.get("item", "Work")[:200], "description": "; ".join(terms.get("deliverables") or [])[:1000],
                       "quantity": "1", "unit_amount": money(price)}],
            "configuration": cfg}
    created = call("POST", "/v2/invoicing/invoices", body)
    href = created.get("href", "")
    inv_id = href.rstrip("/").split("/")[-1] if href else created.get("id")
    return {"id": inv_id}


def disputes() -> list:
    return call("GET", "/v1/customer/disputes").get("items", [])


def dispute(dispute_id: str) -> dict:
    return call("GET", f"/v1/customer/disputes/{dispute_id}")


def provide_evidence(dispute_id: str, pdf: bytes, note: str) -> dict:
    """Send the room's evidence report to PayPal (multipart: input JSON + the PDF)."""
    boundary = uuid.uuid4().hex
    meta = json.dumps({"evidences": [{"evidence_type": "OTHER", "notes": note[:2000],
                                      "documents": [{"name": "dealroom-evidence.pdf"}]}]})
    parts = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"input\"; filename=\"input.json\"\r\n"
             f"Content-Type: application/json\r\n\r\n{meta}\r\n"
             f"--{boundary}\r\nContent-Disposition: form-data; name=\"file1\"; filename=\"dealroom-evidence.pdf\"\r\n"
             f"Content-Type: application/pdf\r\n\r\n").encode() + pdf + f"\r\n--{boundary}--\r\n".encode()
    return call("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", raw=parts,
                ctype=f"multipart/form-data; boundary={boundary}")


def verify_webhook(headers, body: dict) -> bool:
    """Ask PayPal whether this webhook really came from PayPal (needs PAYPAL_WEBHOOK_ID)."""
    wid = os.environ.get("PAYPAL_WEBHOOK_ID", "").strip()
    if not wid:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    try:
        res = call("POST", "/v1/notifications/verify-webhook-signature", {
            "auth_algo": h.get("paypal-auth-algo"), "cert_url": h.get("paypal-cert-url"),
            "transmission_id": h.get("paypal-transmission-id"), "transmission_sig": h.get("paypal-transmission-sig"),
            "transmission_time": h.get("paypal-transmission-time"), "webhook_id": wid, "webhook_event": body})
    except PayPalError:
        return False
    return res.get("verification_status") == "SUCCESS"
