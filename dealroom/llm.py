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
