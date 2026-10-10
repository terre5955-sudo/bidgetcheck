import difflib
import json
import re
import time
from typing import Any

from . import db, llm, rules
from .config import LLM_DELAY_MS, MIN_VERIFIED_RATIO
from .extract import Chunk, Page, chunk_pages, load_pages

EXTRACT_PROMPT = llm.load_prompt("extract_requirements.md")
VERDICT_PROMPT = llm.load_prompt("verdict.md")

MAX_REQUIREMENTS = 150


def _norm(text: str) -> str:
    text = text.lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: str, chunk: Chunk, claimed_page: int) -> tuple[bool, str, int]:
    quote = (quote or "").strip()
    if not quote or len(quote) < 12:
        return False, "", claimed_page

    if "..." in quote or quote.endswith("…"):
        parts = [p for p in re.split(r"\.\.\.|…", quote) if len(_norm(p)) > 15]
        quote = " ".join(p.strip() for p in parts)

    target_pages = [claimed_page] + [p for p in chunk.pages if abs(p - claimed_page) <= 2]
    for page in dict.fromkeys(target_pages):
        page_text = chunk.pages.get(page, "")
        if not page_text:
            continue
        if _norm(quote) and _norm(quote) in _norm(page_text):
            return True, quote, page

    best_ratio, best_page, best_sentence = 0.0, claimed_page, ""
    for page in dict.fromkeys(target_pages):
        page_text = chunk.pages.get(page, "")
        if not page_text:
            continue
        sentences = [s for s in rules.split_sentences(page_text)] or [page_text]
        for sentence in sentences:
            ratio = difflib.SequenceMatcher(None, _norm(quote), _norm(sentence)).ratio()
            if ratio > best_ratio:
                best_ratio, best_page, best_sentence = ratio, page, sentence

    if best_ratio >= 0.72:
        return True, best_sentence[:1200], best_page
    return False, "", claimed_page


def _req(hit: dict, kind: str, severity: int, document_required: str | None = None,
        category: str = "") -> dict:
    return {
        "page": hit["page"],
        "quote": hit["sentence"],
        "requirement": hit["sentence"][:300],
        "kind": kind,
        "severity": max(1, min(5, severity)),
        "source": "rules",
        "category": category or hit.get("category", ""),
        "verified": True,
        "document_required": document_required,
        "threshold": None,
        "deadline": None,
    }


def _fallback_requirements(scan: dict) -> list[dict]:
    out: list[dict] = []
    for hit in scan.get("disqualifiers", []):
        out.append(_req(hit, "mandatory", 5, category="disqualification"))
    for hit in scan.get("obligations", []):
        out.append(_req(hit, "mandatory", hit.get("severity", 4),
                        category=hit.get("category", "mandatory_requirement")))
    for hit in scan.get("enumerated", []):
        out.append(_req(hit, "administrative", 3, document_required=hit["sentence"][:160],
                        category="enumerated_requirement"))
    for hit in scan.get("required_documents", []):
        out.append(_req(hit, "administrative", 3, category="required_document"))
    for hit in scan.get("rejection_risks", []):
        out.append(_req(hit, "administrative", hit.get("severity", 3), category="rejection_risk"))
    return out


def _clean(raw: Any) -> list[dict]:
    if isinstance(raw, dict):
        items = raw.get("requirements") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        req = str(item.get("requirement") or item.get("text") or "").strip()
        if not req:
            continue
        try:
            page = int(item.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        try:
            severity = int(item.get("severity") or 3)
        except (TypeError, ValueError):
            severity = 3
        kind = str(item.get("kind") or "mandatory").lower()
        if kind not in {"mandatory", "scoring", "administrative"}:
            kind = "mandatory"
        cleaned.append({
            "page": page,
            "quote": str(item.get("quote") or "").strip(),
            "requirement": req[:800],
            "kind": kind,
            "severity": max(1, min(5, severity)),
            "document_required": item.get("document_required"),
            "threshold": item.get("threshold"),
            "deadline": item.get("deadline"),
        })
    return cleaned


def _dedupe(items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in items:
        key = _norm(item["requirement"])[:120]
        if not key:
            continue
        current = seen.get(key)
        if current is None:
            seen[key] = item
            continue
        if item["severity"] > current["severity"] or not current.get("quote") and item.get("quote"):
            seen[key] = item
    return list(seen.values())


def extract_requirements(chunks: list[Chunk], scan: dict) -> tuple[list[dict], dict]:
    if not llm.available():
        items = _fallback_requirements(scan)
        return _dedupe(items), {"engine": "rules", "verified_ratio": 1.0,
                                "chunks_ok": 0, "chunks_failed": 0, "hallucinations_blocked": 0}

    collected: list[dict] = []
    ok = failed = blocked = 0
    rate_limited = False

    for chunk in chunks:
        if rate_limited:
            failed += 1
            continue
        user = (
            "Excerpt follows.\n\n" + chunk.text +
            "\n\nExtract every requirement from pages " + str(chunk.start_page) + " to " + str(chunk.end_page) + "."
        )
        try:
            raw = llm.complete(EXTRACT_PROMPT, user, json_mode=True, temperature=0.0)
        except llm.RateLimited:
            rate_limited = True
            db.log_event("", "rate_limited", f"at chunk {chunk.index}, falling back to rules")
            continue
        except Exception as exc:
            failed += 1
            db.log_event("", "chunk_failed", f"chunk {chunk.index}: {exc}")
            continue

        if LLM_DELAY_MS:
            time.sleep(LLM_DELAY_MS / 1000)

        parsed = llm.parse_json(raw)
        if parsed is None:
            failed += 1
            continue
        ok += 1

        for item in _clean(parsed):
            verified, corrected, page = verify_quote(item["quote"], chunk, item["page"])
            if not verified:
                blocked += 1
                continue
            item["quote"] = corrected
            item["page"] = page
            item["verified"] = True
            item["source"] = "llm"
            collected.append(item)

    if rate_limited and ok == 0:
        db.log_event("", "rate_limited_fallback", "no chunk completed before the limit")
        items = _fallback_requirements(scan)
        return _dedupe(items), {"engine": "rules", "verified_ratio": 1.0, "chunks_ok": 0,
                                "chunks_failed": failed, "hallucinations_blocked": blocked,
                                "rate_limited": True}

    if ok == 0:
        items = _fallback_requirements(scan)
        return _dedupe(items), {"engine": "rules", "verified_ratio": 1.0, "chunks_ok": 0,
                                "chunks_failed": failed, "hallucinations_blocked": blocked}

    ratio = len(collected) / max(1, len(collected) + blocked)
    if ratio < MIN_VERIFIED_RATIO:
        db.log_event("", "low_verified_ratio", f"ratio={ratio:.2f}, using rules engine")
        items = _fallback_requirements(scan)
        return _dedupe(items), {"engine": "rules", "verified_ratio": ratio, "chunks_ok": ok,
                                "chunks_failed": failed, "hallucinations_blocked": blocked}

    merged = _dedupe(collected + _fallback_requirements(scan))
    merged.sort(key=lambda r: (-r["severity"], r["page"]))
    return merged[:MAX_REQUIREMENTS], {"engine": "llm", "verified_ratio": round(ratio, 3),
                                       "chunks_ok": ok, "chunks_failed": failed,
                                       "hallucinations_blocked": blocked}


def _normalize_verdict(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    verdict = str(raw.get("verdict") or "").lower()
    if verdict not in {"bid", "conditional", "no_bid"}:
        verdict = "conditional"
    try:
        confidence = max(0, min(100, int(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0
    blockers, actions = [], []
    for item in raw.get("blockers") or []:
        if isinstance(item, dict) and item.get("requirement"):
            blockers.append({"requirement": str(item["requirement"])[:400],
                             "page": int(item.get("page") or 0),
                             "why": str(item.get("why") or "")[:400]})
    for action in raw.get("next_actions") or []:
        if isinstance(action, str) and action.strip():
            actions.append(action.strip()[:300])
    return {
        "verdict": verdict,
        "confidence": confidence,
        "headline": str(raw.get("headline") or "")[:300],
        "blockers": blockers[:20],
        "next_actions": actions[:15],
        "coverage": raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {},
    }


def score_gap(requirements: list[dict], profile: str) -> dict:
    if not requirements or not profile.strip():
        return {"available": False,
                "note": "Gap scoring needs an LLM and a company profile."}
    if not llm.available():
        return {"available": False,
                "note": "Gap scoring needs an LLM and a company profile."}

    index = [
        {"page": r["page"], "requirement": r["requirement"], "kind": r["kind"],
         "severity": r["severity"], "deadline": r.get("deadline"),
         "threshold": r.get("threshold"), "document_required": r.get("document_required")}
        for r in requirements[:120]
    ]
    user = (
        "COMPANY PROFILE:\n" + profile.strip()[:6000] +
        "\n\nVERIFIED REQUIREMENTS:\n" + json.dumps(index, ensure_ascii=False) +
        "\n\nAssess coverage."
    )

    try:
        raw = llm.complete(VERDICT_PROMPT, user, json_mode=True, temperature=0.0)
    except Exception as exc:
        return {"available": False, "note": f"LLM error: {exc}"}

    parsed = llm.parse_json(raw)
    verdict = _normalize_verdict(parsed)
    if not verdict:
        return {"available": False, "note": "Could not parse verdict from model output."}
    verdict["available"] = True
    return verdict


def analyze_file(path: str, filename: str, profile: str = "", analysis_id: str = "",
                 max_pages: int | None = None) -> dict:
    pages: list[Page] = load_pages(path, max_pages=max_pages)
    chunks = chunk_pages(pages)
    scan = rules.scan(pages)
    requirements, extraction = extract_requirements(chunks, scan)
    verdict = score_gap(requirements, profile) if profile.strip() else {
        "available": False, "note": "No company profile supplied - verdict skipped."}

    mandatory = [r for r in requirements if r["kind"] == "mandatory"]
    severity_counts = {str(s): sum(1 for r in requirements if r["severity"] == s) for s in range(5, 0, -1)}

    return {
        "document": {
            "filename": filename,
            "pages": len(pages),
            "chunks": len(chunks),
            "characters": sum(len(p.text) for p in pages),
        },
        "engine": {
            "llm": llm.available(),
            **extraction,
            **llm.status(),
        },
        "requirements": sorted(requirements, key=lambda r: (-r["severity"], r["page"])),
        "deadlines": scan["deadlines"],
        "rejection_risks": scan.get("rejection_risks", []),
        "special_checks": scan["special_checks"],
        "financial": scan["financial"],
        "verdict": verdict,
        "stats": {
            **scan["stats"],
            "requirements": len(requirements),
            "mandatory": len(mandatory),
            "by_severity": severity_counts,
        },
    }