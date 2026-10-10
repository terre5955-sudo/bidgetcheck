import json
import re
from functools import lru_cache

import httpx

from .config import (
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


def load_prompt(name: str) -> str:
    from .config import PROMPTS_DIR

    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


@lru_cache(maxsize=1)
def available() -> bool:
    if LLM_PROVIDER == "none":
        return False
    if LLM_PROVIDER == "ollama":
        try:
            r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False
    try:
        r = httpx.get(f"{OPENAI_BASE_URL}/models", timeout=3.0,
                       headers={"Authorization": f"Bearer {OPENAI_API_KEY}"})
        return r.status_code < 500
    except Exception:
        return False


def status() -> dict:
    ok = available()
    return {
        "enabled": ok,
        "provider": LLM_PROVIDER if ok else "none",
        "model": OLLAMA_MODEL if (ok and LLM_PROVIDER == "ollama") else (OPENAI_MODEL if ok else ""),
        "hint": "" if ok else "LLM offline - running deterministic engine only",
    }


def parse_json(raw: str) -> dict | list | None:
    if not raw:
        return None
    text = raw.strip()
    candidates = [text]
    m = JSON_FENCE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


def complete(system: str, user: str, json_mode: bool = True, temperature: float = 0.0) -> str:
    if not available():
        raise RuntimeError("LLM provider is not reachable")
    if LLM_PROVIDER == "ollama":
        payload = {
            "model": OLLAMA_MODEL,
            "stream": False,
            "format": "json" if json_mode else "",
            "options": {"temperature": temperature, "num_ctx": OLLAMA_NUM_CTX, "num_predict": 4096},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        resp = httpx.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=LLM_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")

    payload = {
        "model": OPENAI_MODEL,
        "temperature": temperature,
        "response_format": {"type": "json_object"} if json_mode else None,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    resp = httpx.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        json=payload,
        timeout=LLM_TIMEOUT,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]