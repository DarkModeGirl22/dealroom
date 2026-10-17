# DealRoom

*Protection for digital freelancers paid through PayPal: every message, payment and delivery is recorded, so if you did nothing wrong, the record proves it.*
**Try it online:** LIVE_LINK_PENDING (choose a story; each visitor gets a private copy). The first load can take up to a minute on the free server.

## The problem

Illustrators, designers, video editors and writers find clients on Instagram, Discord or Reddit, agree in DMs, deliver a file and get paid by PayPal. When something goes wrong, their proof is scattered screenshots, and a digital file has no tracking number.

- **"I never received it"** is hard to disprove. **"Not as described"** claims are never covered by PayPal Seller Protection.
- Buyers have **up to 180 days** to open a dispute.
- Merchants win about **44%** of the disputes they fight, but recover only about **11%** of all disputed money, mostly because they give up on the rest: collecting evidence by hand is too much work ([Chargebacks911](https://chargebacks911.com/?p=22533)).
- **64 million Americans** freelanced in 2023 ([Upwork](https://investors.upwork.com/news-releases/news-release-details/upwork-study-finds-64-million-americans-freelanced-2023-adding)).
- Scams target them too: overpayment tricks, fake PayPal invoices, "pay me as Friends & Family".

## What DealRoom does

Everything about a job happens in one room, and everything is recorded with a time:

1. **The chat becomes the contract.** NVIDIA Nemotron reads the conversation and drafts the terms (price, deposit, revisions, delivery). Both people accept them.
2. **One tap makes the PayPal invoice** and a PDF copy, written from the accepted terms: tips off, the deposit as the minimum first payment.
3. **The client pays with the PayPal button in the room.** The order is created by the server from the terms, so the amount is locked: the overpayment trick is impossible. Refunds only go back to the original payment.
4. **The freelancer delivers the file in the room.** Nemotron looks at it and checks it against the terms ("Blue background ✗"). The client's download is recorded.
5. **The AI watches for scams** (Friends & Family, "send the difference back", links) and warns calmly, only when it matters.
6. **The private honesty check.** Before someone sends a claim the record contradicts ("I never received it"), only they see: *"The record shows you downloaded it on Oct 12 at 14:03."* They can correct themselves without looking like a liar. If they send it anyway, both people see the same facts.
7. **If a dispute happens,** one click builds the evidence report for PayPal: a neutral timeline, each side's statement, and a short assessment. Every sentence points to the record, and it is sent through PayPal's Disputes API.

DealRoom never decides a dispute and never moves money on its own. PayPal decides; the AI reads, explains and warns.

## How PayPal is used (sandbox)

| PayPal capability | What it does in DealRoom | Code |
|---|---|---|
| Orders API v2 + JavaScript SDK buttons | Payment inside the room; the server sets the amount from the accepted terms | `dealroom/billing.py`, `static/room.js` |
| Invoicing API v2 | Invoice written from the terms; `allow_tip: false`, partial payment with a minimum (the deposit) | `dealroom/paypal.py` |
| Refunds API | Refunds only to the original capture | `dealroom/paypal.py` |
| Webhooks + signature verification | Payments, refunds, reversals and disputes reported by PayPal appear in the right room | `dealroom/app.py`, `dealroom/disputes.py` |
| Disputes API (provide evidence) | Sends the room's evidence report (PDF + summary) to a real PayPal dispute | `dealroom/paypal.py`, `dealroom/disputes.py` |

## How AI is used: NVIDIA Nemotron on Nebius Token Factory

| Job | Model |
|---|---|
| Turn the chat into terms; write the dispute statements and assessment | `nvidia/nemotron-3-super-120b-a12b` |
| Check the delivered image against the terms | `nvidia/Nemotron-3.5-Super-VL` |
| Classify what a message claims (for scam signals and the honesty check) | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` |

**AI reads, code checks.** The AI never writes a fact on its own authority:

- A price, deposit or number of revisions in the drafted terms is kept only if it was actually said in the chat.
- The facts in the honesty check come from the record (downloads, payments, file check), written by code; the AI only says what a message claims.
- In the dispute report, every sentence must cite timeline events; a sentence is removed if it cites nothing, or contains a number or a name that isn't in the events it cites. So the AI can't help anyone lie, including the person asking for the report. If the record supports the client, it tells the freelancer privately before anything is sent.
- Spending limits: $1 a day and $10 in total by default; without a key, the room still works without the AI notes.

## The demo

Open the site and choose a story. Each visitor gets a private copy, deleted after 24 hours. Switch sides with **Maya | Alex** at the top.

- **A new deal:** play both sides. Chat as Alex and Maya, draft the terms, accept, make the invoice, pay with PayPal as Alex, deliver as Maya. To pay, log in to the PayPal window with the sandbox buyer below.
- **A deal gone wrong:** a paid, delivered deal (its past payments are recorded samples). As Alex, write "I never received the file" and see the private check, then open a dispute; as Maya, prepare and send the evidence.

**PayPal sandbox buyer (fake money):** BUYER_EMAIL_PENDING / password: BUYER_PASSWORD_PENDING

All people and files are fictional and marked as samples.

## Run it yourself

1. Python 3.11+, then `pip install -r requirements.txt`.
2. Copy `env.example` to `.env` and add your PayPal sandbox keys (developer.paypal.com, Apps & Credentials) and a Nebius Token Factory key.
3. `python run.py`, then open http://localhost:8000.

Deploy: the `Dockerfile` runs on any container host (we use Render). Set the same variables in the host's environment. For webhooks, add `https://<your host>/paypal/webhook` in your PayPal sandbox app and set `PAYPAL_WEBHOOK_ID`.

## Project layout

```
dealroom/app.py        routes; per-visitor private copies; PayPal webhook
dealroom/room.py       the room, messages, notes, demo stories
dealroom/terms.py      chat to terms (Nemotron), checked against the chat
dealroom/billing.py    invoice, locked PayPal orders, captures
dealroom/delivery.py   file, fingerprint, downloads, AI check against the terms
dealroom/protect.py    scam signals, private honesty check, shared facts
dealroom/disputes.py   neutral timeline, grounded statements, evidence PDF, PayPal disputes
dealroom/paypal.py     PayPal REST client (orders, invoices, refunds, disputes, webhooks)
dealroom/llm.py        Nemotron on Nebius Token Factory, JSON answers, spending limits
```

## AI tools used to build it

NVIDIA Nemotron (inside the product, above). Claude by Anthropic as a coding assistant.

MIT licence. DealRoom isn't affiliated with PayPal.
