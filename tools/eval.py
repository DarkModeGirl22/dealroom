"""Measure the AI's work in DealRoom against known answers, with the real NVIDIA Nemotron models.

    NEBIUS_API_KEY=... python tools/eval.py

Uses a throwaway data folder. Scoring for every value:
  correct - kept and right, or left empty because nothing was agreed
  asked   - left blank for the people to fill in (safe)
  wrong   - kept, but wrong (the failure that matters)
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEALROOM_DATA_DIR"] = tempfile.mkdtemp(prefix="dealroom-eval-")

from dealroom import db, disputes as X, llm, protect as P, room as R, terms as T  # noqa: E402

# chats with the terms they really agreed on: (lines, price, deposit, revisions, deadline word)
CHATS = [
    ([("c", "Hi! Can you draw my dog? Blue background please"), ("f", "Sure, $80 with 2 revisions, ready Friday."),
      ("c", "Can I pay half now?"), ("f", "Yes, $40 now and $40 at delivery.")], 80, 40, 2, "friday"),
    ([("c", "How much for a logo?"), ("f", "$150, 3 rounds of changes, delivered in 5 days."),
      ("c", "Ok, I'll pay everything upfront.")], 150, None, 3, "5 days"),
    ([("c", "Video edit for my channel, 10 minutes?"), ("f", "That's $200. I'd need a $50 deposit."),
      ("c", "Deal. When?"), ("f", "Next Monday.")], 200, 50, None, "monday"),
    ([("c", "Could you translate my CV to English?"), ("f", "Sure, $60."), ("c", "Can you do $50?"),
      ("f", "Ok, $50 it is, by Wednesday.")], 50, None, None, "wednesday"),
    ([("c", "Need 2 illustrations for my book cover"), ("f", "$300 for both, 1 revision each"),
      ("c", "Actually make it just 1 illustration"), ("f", "Then $180 with 1 revision. Half upfront.")], 180, 90, 1, None),
    ([("c", "Hi, can you make me a website banner?"), ("f", "Happy to! What size and colors?"),
      ("c", "1500x500, green and white")], None, None, None, None),
]

# messages and what they claim (empty = no claim)
MESSAGES = [
    ("I never received the file", {"not_received"}), ("The background isn't blue like I asked", {"not_as_described"}),
    ("You still haven't paid me the final $40", {"not_paid"}), ("It's 3 days late, where is it?", {"late"}),
    ("I want my money back", {"wants_refund"}), ("Love it, thank you so much!", set()),
    ("Can you make the eyes a bit bigger?", set()), ("Sent the deposit just now", set()),
    ("Got the file, downloading it now", set()), ("What time works for a call?", set()),
]


def score(got, want):
    if got is None:
        return "correct" if want is None else "asked"   # nothing agreed and nothing kept = right
    if want is None:
        return "wrong"
    if isinstance(want, str):
        return "correct" if want in str(got).lower() else "wrong"
    return "correct" if abs(float(got) - float(want)) < 0.01 else "wrong"


def tally(xs):
    return {k: xs.count(k) for k in ("correct", "asked", "wrong")}


def meter():
    u = llm.usage()
    return u["calls"], u["total_usd"]


def eval_terms(log):
    marks, t0 = [], time.time()
    c0 = meter()
    for i, (lines, price, dep, rev, dl) in enumerate(CHATS, 1):
        chat = [{"id": j, "name": "Client" if w == "c" else "Freelancer", "role": "client" if w == "c" else "freelancer",
                 "text": t} for j, (w, t) in enumerate(lines, 1)]
        raw = llm.ask(T.SYSTEM, "\n".join(f"[{m['id']}] {m['name']} ({m['role']}): {m['text']}" for m in chat),
                      task="smart", max_tokens=600) or {}
        terms, _ = T.check(raw, chat)
        row = [score(terms["price"], price), score(terms["deposit"], dep), score(terms["revisions"], rev),
               score(terms["deadline"], dl)]
        marks += row
        log.append(f"chat {i}: price {terms['price']} deposit {terms['deposit']} revisions {terms['revisions']} "
                   f"deadline {terms['deadline']} -> {row}")
    c1 = meter()
    return tally(marks), c1[0] - c0[0], c1[1] - c0[1], time.time() - t0


def eval_files(log):
    t0, c0 = time.time(), meter()
    want = {"biscuit_portrait_blue.png": "yes", "biscuit_portrait_red.png": "no"}
    right = 0
    for name, expect in want.items():
        out = llm.look("Terms: a digital portrait of a dog with a blue background. Check: \"Blue background\". "
                       "Reply JSON only: {\"checks\": [{\"requirement\": \"Blue background\", \"result\": \"yes\" or "
                       "\"no\" or \"unclear\"}]}", ROOT / "samples" / name) or {}
        res = ((out.get("checks") or [{}])[0]).get("result")
        right += res == expect
        log.append(f"file {name}: {res} (expected {expect})")
    c1 = meter()
    return right, len(want), c1[0] - c0[0], c1[1] - c0[1], time.time() - t0


def eval_claims(log):
    t0, c0 = time.time(), meter()
    tp = fp = fn = 0
    for text, want in MESSAGES:
        got = P._claims(text)
        hit = bool(got & want) if want else not got
        tp += bool(want) and hit
        fn += bool(want) and not hit
        fp += (not want) and bool(got)
        if not hit:
            log.append(f"claim \"{text}\": got {sorted(got)}, expected {sorted(want)}")
    c1 = meter()
    return tp, fn, fp, c1[0] - c0[0], c1[1] - c0[1], time.time() - t0


def eval_reports(log, runs=3):
    t0, c0 = time.time(), meter()
    conn = db.connect(Path(os.environ["DEALROOM_DATA_DIR"]) / "eval.sqlite3")
    kept = removed = 0
    supports = []
    for _ in range(runs):
        R.seed(conn, "wrong")
        rm = R.get(conn, 1)
        alex, maya = R.person(conn, "alex"), R.person(conn, "maya")
        X.open_dispute(conn, rm, alex, "not_received", "I never received the file")
        ev = X.timeline(conn, rm)
        events = {e["id"]: e for e in ev}
        lines = "\n".join(f"[{e['id']}] {e['ts']} · {e['what']} (source: {e['src']})" for e in ev)
        raw = llm.ask(X.WRITE, f"Timeline:\n{lines}\n\nClient's claim: Item not received: \"I never received the file\"",
                      task="smart", max_tokens=900) or {}
        for k in ("freelancer", "client", "assessment"):
            items = [i for i in (raw.get(k) or []) if isinstance(i, dict)]
            n_kept = len([s for s in X._grounded(items, events).split("] ") if s.strip()]) if items else 0
            n_kept = min(n_kept, len(items))
            kept += n_kept
            removed += len(items) - n_kept
        supports.append(raw.get("supports"))
    log.append(f"reports: supports {supports}")
    c1 = meter()
    return kept, removed, supports, c1[0] - c0[0], c1[1] - c0[1], time.time() - t0


def main():
    if not llm.key():
        sys.exit("Set NEBIUS_API_KEY first.")
    log = []
    tt, tcalls, tusd, tsec = eval_terms(log)
    fr, fn_, fcalls, fusd, fsec = eval_files(log)
    tp, fn, fp, ccalls, cusd, csec = eval_claims(log)
    kept, removed, sup, rcalls, rusd, rsec = eval_reports(log)
    fmt = lambda t: f"{t['correct']} correct, {t['asked']} left for the people, **{t['wrong']} wrong**"
    total_calls = tcalls + fcalls + ccalls + rcalls
    total_usd = tusd + fusd + cusd + rusd
    print(f"Run on {time.strftime('%Y-%m-%d')} against Nebius Token Factory: {total_calls} model calls, ${total_usd:.3f}.\n")
    print("| What the AI does | Model | Result |")
    print("|---|---|---|")
    print(f"| Turn 6 chats into terms (24 values; one chat changes the price, one never agrees on terms) | Nemotron 3 Super | {fmt(tt)} |")
    print(f"| Check a delivered image against the terms (blue background vs red) | Nemotron 3.5 Super VL | {fr} of {fn_} right |")
    print(f"| Recognise what a message claims (10 messages, 5 claims) | Nemotron 3 Nano | {tp} of {tp + fn} claims found, {fp} false alarms |")
    print(f"| Dispute report, 3 runs: sentences kept vs removed by the grounding filter | Nemotron 3 Super | {kept} kept, {removed} removed; "
          f"sided with the freelancer in {sup.count('freelancer')} of {len(sup)} (the record supports her) |")
    print("\nDetails:\n")
    for line in log:
        print("    " + line)


if __name__ == "__main__":
    main()
