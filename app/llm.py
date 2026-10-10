import json
import os
import re
import time
from typing import Any

import httpx

from .config import (
    GEMINI_API_KEY,
    GEMINI_BASE,
    GEMINI_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_NUM_CTX,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

GROQ_BASE = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.getenv("BC_GROQ_MODEL", "openai/gpt-oss-120b")

_openai_style = {"openai", "groq", "openrouter", "together"}
_all_known = _openai_style | {"ollama", "gemini", "none"}


class RateLimited(Exception):
    pass


def _base_and_model() -> tuple[str, str]:
    if LLM_PROVIDER == "groq":
        return GROQ_BASE, GROQ_MODEL
    return OPENAI_BASE_URL, OPENAI_MODEL


def _key() -> str:
    if LLM_PROVIDER == "groq":
        return os.getenv("BC_GROQ_API_KEY") or os.getenv("GROQ_API_KEY") or ""
    return OPENAI_API_KEY


def load_prompt(name: str) -> str:
    from .config import PROMPTS_DIR

    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _probe() -> bool:
    if LLM_PROVIDER in ("none", "", None):
        return False
    try:
        if LLM_PROVIDER == "ollama":
            return httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0).status_code == 200
        if LLM_PROVIDER == "gemini":
            if not GEMINI_API_KEY:
                return False
            resp = httpx.get(
                f"{GEMINI_BASE}/models?key={GEMINI_API_KEY}",
                timeout=4.0, headers={"x-goog-api-client": "bidgetcheck/1.0"})
            return resp.status_code < 400
        if LLM_PROVIDER in _openai_style:
            if not _key():
                return False
            base, _ = _base_and_model()
            resp = httpx.get(f"{base}/models", timeout=4.0,
                             headers={"Authorization": f"Bearer {_key()}"})
            return resp.status_code < 400
    except Exception:
        return False
    return False


_cache: dict[str, Any] = {"at": 0.0, "value": False}
PROBE_TTL = 30.0


def available(force: bool = False) -> bool:
    now = time.monotonic()
    if not force and now - _cache["at"] < PROBE_TTL:
        return bool(_cache["value"])
    value = _probe()
    _cache.update({"at": now, "value": value})
    return value


def reset_cache() -> None:
    _cache.update({"at": 0.0, "value": False})


def provider_label() -> str:
    if LLM_PROVIDER == "ollama":
        return OLLAMA_MODEL
    if LLM_PROVIDER == "gemini":
        return GEMINI_MODEL
    if LLM_PROVIDER == "groq":
        return GROQ_MODEL
    return OPENAI_MODEL


def status() -> dict:
    ok = available()
    if not ok:
        hint = {
            "gemini": "No Gemini key. Set GEMINI_API_KEY to enable AI extraction.",
            "groq": "No Groq key. Set BC_GROQ_API_KEY to enable AI extraction.",
            "openai": "No API key. Set BC_OPENAI_API_KEY to enable AI extraction.",
            "ollama": "Ollama is not running. Start it, or switch provider to gemini/groq.",
        }.get(LLM_PROVIDER, "LLM disabled. Running deterministic engine only.")
        return {"enabled": False, "provider": "none", "model": "",
                "requested_provider": LLM_PROVIDER, "hint": hint}
    return {"enabled": True, "provider": LLM_PROVIDER, "model": provider_label(),
            "requested_provider": LLM_PROVIDER, "hint": ""}


def parse_json(raw: str) -> Any:
    if not raw:
        return None
    text = raw.strip()
    candidates = [text]
    fenced = JSON_FENCE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise RateLimited("provider rate limit reached")
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM provider returned {resp.status_code}: {resp.text[:300]}")


def _ollama(system: str, user: str, json_mode: bool, temperature: float) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json" if json_mode else "",
        "options": {"temperature": temperature, "num_ctx": OLLAMA_NUM_CTX, "num_predict": 4096},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    resp = httpx.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=LLM_TIMEOUT)
    _raise_for(resp)
    return resp.json().get("message", {}).get("content", "")


def _gemini(system: str, user: str, json_mode: bool, temperature: float) -> str:
    config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": 4096}
    if json_mode:
        config["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": config,
    }
    url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    resp = httpx.post(url, json=payload, timeout=LLM_TIMEOUT,
                      headers={"x-goog-api-client": "bidgetcheck/1.0"})
    _raise_for(resp)
    candidates = resp.json().get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts)


def _openai_chat(system: str, user: str, json_mode: bool, temperature: float) -> str:
    base, model = _base_and_model()
    headers = {"Authorization": f"Bearer {_key()}"}
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 4096,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    resp = httpx.post(f"{base}/chat/completions", json=body, timeout=LLM_TIMEOUT, headers=headers)
    if resp.status_code == 400 and json_mode:
        body.pop("response_format", None)
        resp = httpx.post(f"{base}/chat/completions", json=body, timeout=LLM_TIMEOUT, headers=headers)
    _raise_for(resp)
    return resp.json()["choices"][0]["message"].get("content", "") or ""


def complete(system: str, user: str, json_mode: bool = True, temperature: float = 0.0) -> str:
    if not available():
        raise RuntimeError("LLM provider is not reachable")
    if LLM_PROVIDER == "ollama":
        return _ollama(system, user, json_mode, temperature)
    if LLM_PROVIDER == "gemini":
        return _gemini(system, user, json_mode, temperature)
    if LLM_PROVIDER in _openai_style:
        return _openai_chat(system, user, json_mode, temperature)
    raise RuntimeError(f"unknown LLM provider: {LLM_PROVIDER}")