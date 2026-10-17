"""The AI witness: NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible API).

Every call returns strict JSON, has a time limit and a spending cap, and its answer is checked
by DealRoom's code before anyone sees it. Without a key, the room still works; only the AI
notes are skipped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from . import db

URL = os.environ.get("DEALROOM_NEBIUS_URL", "https://api.tokenfactory.nebius.com/v1").rstrip("/")
MODELS = {
    "fast": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",      # quick checks on every message
    "smart": "nvidia/nemotron-3-super-120b-a12b",         # terms, statements, the evidence report
    "vision": "nvidia/Nemotron-3.5-Super-VL",             # does the delivered file match the terms?
}
LABELS = {"fast": "NVIDIA Nemotron 3 Nano", "smart": "NVIDIA Nemotron 3 Super", "vision": "NVIDIA Nemotron 3.5 Super VL"}
PRICES = {MODELS["fast"]: (0.06, 0.24), MODELS["smart"]: (0.30, 0.90), MODELS["vision"]: (0.30, 0.90)}
DAILY_USD = float(os.environ.get("DEALROOM_AI_DAILY_USD", "1.00"))
TOTAL_USD = float(os.environ.get("DEALROOM_AI_TOTAL_USD", "10"))
TIMEOUT = 45
_lock = threading.Lock()


def key() -> str:
    return os.environ.get("NEBIUS_API_KEY", "").strip()


def _usage_path() -> Path:
    return db.DATA_DIR / "ai_usage.json"


def usage() -> dict:
    try:
        u = json.loads(_usage_path().read_text())
    except (OSError, ValueError):
        u = {}
    if u.get("day") != date.today().isoformat():
        u["day"], u["today_usd"] = date.today().isoformat(), 0.0
    u.setdefault("total_usd", 0.0)
    u.setdefault("calls", 0)
    return u


def available() -> bool:
    u = usage()
    return bool(key()) and u["today_usd"] < DAILY_USD and u["total_usd"] < TOTAL_USD


def _record(model, tin, tout):
    pin, pout = PRICES.get(model, (1, 3))
    cost = (tin * pin + tout * pout) / 1e6
    with _lock:
        u = usage()
        u["today_usd"] += cost
        u["total_usd"] += cost
        u["calls"] += 1
        _usage_path().parent.mkdir(parents=True, exist_ok=True)
        _usage_path().write_text(json.dumps(u))


def parse_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    body = text[start:]
    for extra in ("", "}", "]}", "}}", "\"}"):
        try:
            return json.loads(body + extra)
        except ValueError:
            continue
    return None


def _call(task: str, messages: list, max_tokens: int, json_mode: bool = True):
    if not available():
        return None
    model = MODELS[task]
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.1,
            "chat_template_kwargs": {"enable_thinking": False}}
    if json_mode and task != "vision":
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(f"{URL}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    u = out.get("usage") or {}
    _record(model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    content = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return parse_json(content)


def ask(system: str, user: str, task: str = "fast", max_tokens: int = 700):
    return _call(task, [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens)


def look(prompt: str, image_path: Path, max_tokens: int = 700):
    data = base64.b64encode(Path(image_path).read_bytes()).decode()
    mime = "image/png" if str(image_path).lower().endswith(".png") else "image/jpeg"
    msg = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}]}]
    return _call("vision", msg, max_tokens)


class _SameCallIds:
    """Nebius streams a tool call under one call_id and lists it under another in `response.completed`.
    AG Studio's agents match tool results by call_id, so a mismatch loses the result and the agent asks again,
    in a loop. This rewrites the final ids to the streamed ones, one server-sent event line at a time."""

    def __init__(self):
        self.buf = b""
        self.ids = {}   # output item id -> the call_id the stream used

    def _line(self, line: bytes) -> bytes:
        if not line.startswith(b"data: "):
            return line
        try:
            ev = json.loads(line[6:])
        except ValueError:
            return line
        t = ev.get("type")
        if t in ("response.output_item.added", "response.output_item.done"):
            item = ev.get("item") or {}
            if item.get("type") == "function_call" and item.get("id") and item.get("call_id"):
                self.ids.setdefault(item["id"], item["call_id"])
            return line
        if t == "response.completed":
            changed = False
            for item in (ev.get("response") or {}).get("output") or []:
                cid = self.ids.get(item.get("id"))
                if item.get("type") == "function_call" and cid and item.get("call_id") != cid:
                    item["call_id"], changed = cid, True
            if changed:
                return b"data: " + json.dumps(ev).encode()
        return line

    def feed(self, chunk: bytes) -> bytes:
        self.buf += chunk
        *lines, self.buf = self.buf.split(b"\n")
        return b"".join(self._line(l) + b"\n" for l in lines)

    def flush(self) -> bytes:
        rest, self.buf = self.buf, b""
        return self._line(rest) if rest else b""


def responses_proxy(body: dict):
    """The dashboard's AI assistant (AG Studio) speaks OpenAI's Responses API; Nebius serves it for Nemotron.
    The key stays on the server, the model is fixed, output is capped, and the spending limits apply.
    Yields the upstream bytes (a server-sent event stream when the request asks for one)."""
    if not available():
        yield b'data: {"type":"error","message":"The AI assistant is resting: today\'s AI budget is used up."}\n\n'
        return
    body = dict(body)
    body["model"] = MODELS["smart"]
    body["max_output_tokens"] = min(int(body.get("max_output_tokens") or 4000), 4000)
    body.setdefault("reasoning", {"effort": "low"})
    for k in ("store", "user", "metadata"):
        body.pop(k, None)
    req = urllib.request.Request(f"{URL}/responses", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key()}", "Content-Type": "application/json"})
    tin = len(json.dumps(body)) // 4
    tout = 0
    dbg = os.environ.get("DEALROOM_AI_DEBUG")
    if dbg:
        with open(dbg, "a") as f:
            f.write("\n=== REQUEST " + json.dumps({k: (v if k not in ("input", "tools") else f"[{len(v)} items]")
                                                   for k, v in body.items()})[:1500] + "\n")
            f.write("TOOLS " + ", ".join(t.get("name", "?") for t in body.get("tools") or []) + "\n")
            f.write("LAST INPUT " + json.dumps((body.get("input") or [])[-2:])[:1500] + "\n")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            fix = _SameCallIds()
            while True:
                chunk = r.read1(4096) if hasattr(r, "read1") else r.read(4096)
                if not chunk:
                    break
                tout += len(chunk) // 8
                if dbg:
                    with open(dbg, "a") as f:
                        f.write(chunk.decode("utf-8", "ignore"))
                out = fix.feed(chunk)
                if out:
                    yield out
            rest = fix.flush()
            if rest:
                yield rest
    except urllib.error.HTTPError as e:
        msg = e.read()[:300].decode("utf-8", "ignore").replace('"', "'")
        yield f'data: {{"type":"error","message":"AI service error {e.code}: {msg}"}}\n\n'.encode()
    except (urllib.error.URLError, TimeoutError, OSError):
        yield b'data: {"type":"error","message":"The AI service did not answer in time."}\n\n'
    finally:
        _record(MODELS["smart"], tin, tout)
