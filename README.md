# DealRoom

*Protection for everyone who buys or sells digital work through PayPal: every message, payment and delivery is recorded, so if you did nothing wrong, the record proves it.*

**Try it online: https://dealroom-v4uu.onrender.com** (choose a story; each visitor gets a private copy). The first load can take up to a minute on the free server.

## Why this matters for PayPal

Millions of people sell digital work to strangers they met online: illustrations, logos, translations, CVs, video edits. Today they agree in DMs and get paid in whatever way the client suggests, and many are pushed toward unprotected methods (Friends & Family, crypto, bank transfers) because nothing protects a file that has no tracking number.

DealRoom makes PayPal the safe default for that market:

1. **Protection brings sellers.** With DealRoom, a PayPal payment comes with the full record: the agreed terms, the delivery, the client's download. Sellers choose PayPal because it's where they're safe.
2. **Sellers bring buyers.** Every deal a seller runs through DealRoom brings a client who pays with PayPal, and who is protected too: the terms are written down and the file is checked against them.
3. **Buyers bring more sellers.** Clients who were treated fairly ask for the same next time. PayPal becomes the reference for paying for digital work, worldwide.

And when a dispute does happen, PayPal receives a clean, grounded evidence report through its own Disputes API, instead of screenshots.

## The problem

Sellers and buyers of digital work both lose today. When something goes wrong, the proof is scattered screenshots, and a digital file has no tracking number.

- **"I never received it"** is hard to disprove. **"Not as described"** claims are never covered by PayPal Seller Protection.
- Buyers have **up to 180 days** to open a dispute, and buyers who are scammed often have no clear terms to point to.
- Merchants win about **44%** of the disputes they fight, but recover only about **11%** of all disputed money, mostly because they give up on the rest: collecting evidence by hand is too much work ([Chargebacks911](https://chargebacks911.com/?p=22533)).
- **64 million Americans** freelanced in 2023 ([Upwork](https://investors.upwork.com/news-releases/news-release-details/upwork-study-finds-64-million-americans-freelanced-2023-adding)).
- Scams target both sides: overpayment tricks, fake PayPal links, "pay me as Friends & Family", "let's move to WhatsApp".

## What DealRoom does

It works like a chat app: **one conversation per person**, listed on the left. Inside a conversation, each job is a **deal** with its own terms, payment, delivery and record. Everything is recorded with a time:

1. **The chat becomes the contract.** As soon as both people agree, NVIDIA Nemotron drafts the terms (price, deposit, revisions, delivery, and the details asked for, like "blue background"). Both accept them.
   - **It keeps up with the chat.** When both agree to a change before anything is paid ("can it be red?" … "sure, red it is"), the terms are re-drafted and both accept again. A request or a counter-offer alone changes nothing. Once money has moved, the agreed terms never change.
   - **New work gets its own deal.** "Can I have another portrait?" after a paid job: DealRoom offers a new deal in the same conversation, moves the messages about it there and drafts its terms ("same price" refers to the earlier deal, which is in the record).
2. **One tap makes the PayPal invoice** and a PDF copy, written from the accepted terms: tips off, the deposit as the minimum first payment.
3. **The client pays with the PayPal button in the room.** The order is created by the server from the terms, so the amount is locked: the overpayment trick is impossible. Refunds only go back to the original payment.
4. **The freelancer delivers the file in the room.** Nemotron checks it against the terms: images by looking at them ("Blue background ✗"), PDFs by reading them ("Translated to English ✓"). The client's download is recorded, with the file's fingerprint.
5. **The AI watches for scams** (Friends & Family, Wise, crypto, "let's move to WhatsApp", "send the difference back", look-alike links) and warns calmly, only when it matters.
6. **The private honesty check, for both sides.** Before someone sends a claim the record contradicts, only they see the facts: a client writing "I never received it" sees *"The record shows you downloaded it on Oct 12 at 14:03"*; a client writing "I already paid" sees that PayPal hasn't confirmed any payment; a freelancer writing "I sent it already" sees that nothing was delivered. It works in French and Spanish too. They can correct themselves without looking like a liar. If they send it anyway, both people see the same facts.
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
| Turn the chat into terms; notice when a message agrees, changes the deal or asks for new work; recognise what a message claims (for the honesty check); write the dispute statements and assessment | `nvidia/nemotron-3-super-120b-a12b` |
| Check a delivered image against the terms | `nvidia/Nemotron-3.5-Super-VL` |
| Check a delivered PDF against the terms (reads its text) | `nvidia/nemotron-3-super-120b-a12b` |
| Play the demo's AI client | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` |

**AI reads, code checks.** The AI never writes a fact on its own authority:

- A price, deposit or number of revisions in the drafted terms is kept only if it was actually said in the chat.
- The facts in the honesty check come from the record (downloads, payments, file check), written by code; the AI only says what a message claims.
- In the dispute report, every sentence must cite timeline events; a sentence is removed if it cites nothing, or contains a number or a name that isn't in the events it cites. So the AI can't help anyone lie, including the person asking for the report. If the record supports the client, it tells the freelancer privately before anything is sent.
- Spending limits: $1 a day and $10 in total by default; without a key, the room still works without the AI notes.

## How well it works, measured

`tools/eval.py` runs the real Nemotron models on samples with known answers. "Wrong" means a wrong value kept without asking the people, the failure that matters. One run: 35 model calls, under 1 cent.

| What the AI does | Model | Result |
|---|---|---|
| Turn 6 chats into terms (24 values; one chat changes the price midway, one never reaches a deal) | Nemotron 3 Super | 24 of 24 right, **0 wrong** |
| Check a delivery against the terms (blue vs red portrait; English vs untranslated CV, as PDF) | Nemotron 3.5 Super VL, Nemotron 3 Super | 4 of 4 right |
| Recognise what a message claims (11 messages, 6 claims, including "Sent the deposit just now" and a promise to pay later) | Nemotron 3 Super | 6 of 6 claims found, 0 false alarms, in 3 runs out of 3 |
| Notice what a message does to the deal: agreed, an offer, a change asked or agreed, new work, or nothing (15 messages, including real typos from testing) | Nemotron 3 Super | 15 of 15 right |
| Write the dispute report (3 runs) | Nemotron 3 Super | Sided with the honest freelancer 3 of 3 times; the grounding filter removed 10 of 20 sentences that weren't fully backed by the record |

**What the first runs caught, and what we changed** (each now has a test):

1. "I'll pay everything upfront" came back as a deposit equal to the price, which would have created a $0 final payment. A deposit equal to the price is now one full payment.
2. "The background isn't blue like I asked" and "I want my money back" never reached the AI: the quick word filter in front of it was too narrow. It now lets every likely complaint through, and the AI decides.
3. The dispute statements first came back without citations, so the filter removed all of them. Each sentence now comes with the list of events it relies on, checked by code.
4. In a hands-on test, a client asked for a second portrait after the first was paid, and nothing happened: the room only knew one job. It now notices new work and changes. Nemotron 3 Nano got only 7 of 11 of these messages right, so this job moved to Nemotron 3 Super with clearer rules (11 of 11).
5. "I'll send half now and the rest when it's done" set off the "never send money back" warning. The pattern now only matches sending money back.
6. In the tricky scenarios below: a counter-offer ("I was hoping for $80") rewrote the terms as if it were agreed, and the AI client then accepted its own counter-offer. Now only an agreed change moves the terms; offers and requests stay in the chat.
7. "I'll pay right now" was treated as a false "I already paid". Promises are now told apart from claims, in code and in the prompt.
8. Adding new claim types made Nemotron 3 Nano miss complaints (2 of 5). Claim recognition moved to Nemotron 3 Super, with a description for each label: 6 of 6, no false alarms.

## Tricky scenarios

`tools/tricky.py` drives the real app with the real models the way people actually behave, and checks what must happen. Latest run: **30 of 30 checks passed** (110 model calls, $0.014).

- Typos and slang ("yes 50$ , 2 revisons, by tuesday" … "ok deal") still become the right terms.
- Haggling: $80, "can you do 60?", "ok 70, final offer", "deal": the terms say $70.
- A change asked but not accepted changes nothing; once accepted ("sure, green it is"), new terms that both must accept again. After a payment, the terms never change.
- False claims, privately checked against the record: "I already sent you the money" (nothing paid), "I sent you the logo already" (nothing delivered), "je n'ai jamais reçu le fichier" (downloaded). "I'll pay right now" is a promise, not a lie: no warning.
- Scams in other words: Wise, Revolut, IBAN, USDT, WhatsApp, "amis et famille", and a look-alike link without "http".
- Small talk and threats ("lol ok", "I'll sue you") change nothing.
- New work asked in French gets its own deal.
- Wrong deliveries: a red portrait for a blue deal, an untranslated CV for a translation deal: both flagged ✗.
- No delivery before both accept the terms. The AI client never accepts an overpriced deal and never claims a payment PayPal hasn't confirmed.

## What's real and what's a sample

| Part | Real or sample |
|---|---|
| PayPal invoice, order, payment button, capture, refunds | Real, in PayPal's sandbox (fake money) |
| PayPal webhooks and dispute evidence | Real when PayPal sends them (signature verified) |
| Every AI result (terms, file check, honesty check, report) | Real, live Nemotron calls |
| "A deal gone wrong" and Lina's CV deal: their past payments | Recorded samples, marked "(sample)" in the room: a real PayPal payment needs a person to approve it |
| People, the dog portrait, the CV | Fictional, marked "SAMPLE · FICTIONAL" |

## Tests

`python -m unittest discover tests` runs 51 tests offline in a few seconds, with the AI and PayPal stood in: private copies per visitor, terms checked against the chat, both sides must accept, amounts locked to the terms, only the client pays, invoice PDF, delivery and download records, the AI file check, scam warnings, the private honesty check (and that the other person never sees it), the grounding filter, the evidence report, the private warning when the record supports the client, and verified PayPal webhooks landing in the right room (once, even if sent twice), the AI client never claiming a payment PayPal hasn't confirmed, terms that follow agreed changes until money moves, requests and offers that change nothing, new work becoming its own deal, one conversation per person where each deal keeps its own record, and other clients never seeing the conversation.

## The demo

Open the site and choose a story. Each visitor gets a private copy, deleted after 24 hours. Maya, the freelancer, has three conversations on the left: Alex (the story), Lina (a finished CV translation, delivered as a PDF) and Sam (a new request). Switch sides with **Maya | Alex** at the top.

- **A new deal, with an AI client:** you play Maya; Alex is played by NVIDIA Nemotron, labelled "Alex (AI)". He negotiates, tries one classic scam (paying as Friends & Family), accepts fair terms, downloads your delivery and reacts to it. He can't claim a payment PayPal hasn't confirmed: code drops any such message. Switch to Alex only to pay with PayPal.
- **A new deal:** play both sides. Chat as Alex and Maya, draft the terms, accept, make the invoice, pay with PayPal as Alex, deliver as Maya. To pay, log in to the PayPal window with the sandbox buyer below.
- **A deal gone wrong:** a paid, delivered deal (its past payments are recorded samples). As Alex, write "I never received the file" and see the private check, then open a dispute; as Maya, prepare and send the evidence. Or ask for another portrait and start a new deal.

**PayPal sandbox buyer (fake money):** `sb-8jnee53408759@personal.example.com` / password: `DealRoomTest1`. In the PayPal window, choose **Log in** (not the card form) and pay with the PayPal balance.

All people and files are fictional and marked as samples.

## Run it yourself

1. Python 3.11+, then `pip install -r requirements.txt`.
2. Copy `env.example` to `.env` and add your PayPal sandbox keys (developer.paypal.com, Apps & Credentials) and a Nebius Token Factory key.
3. `python run.py`, then open http://localhost:8000.

Deploy: the `Dockerfile` runs on any container host (we use Render). Set the same variables in the host's environment. For webhooks, add `https://<your host>/paypal/webhook` in your PayPal sandbox app and set `PAYPAL_WEBHOOK_ID`.

## Project layout

```
dealroom/app.py        routes; per-visitor private copies; PayPal webhook
dealroom/room.py       conversations, deals, messages, notes, demo stories
dealroom/terms.py      chat to terms (Nemotron), checked against the chat
dealroom/billing.py    invoice, locked PayPal orders, captures
dealroom/delivery.py   file, fingerprint, downloads, AI check against the terms (images and PDFs)
dealroom/protect.py    scam signals, private honesty check, shared facts
dealroom/watch.py      keeps up with the chat: agreement, changes, new work as a new deal
dealroom/client_ai.py  the demo's AI-played client (labelled), with its own rules
dealroom/disputes.py   neutral timeline, grounded statements, evidence PDF, PayPal disputes
dealroom/paypal.py     PayPal REST client (orders, invoices, refunds, disputes, webhooks)
dealroom/llm.py        Nemotron on Nebius Token Factory, JSON answers, spending limits
tests/                 51 automated tests
tools/eval.py          the measured evaluation above (needs a Nebius key, under 1 cent)
tools/tricky.py        tricky scenarios on the real app with the real AI (needs a Nebius key, about 1 cent)
```

## AI tools used to build it

NVIDIA Nemotron (inside the product, above). Claude by Anthropic as a coding assistant.

MIT licence. DealRoom isn't affiliated with PayPal.
