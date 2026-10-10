import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="bgc_llm_"))
os.environ["BC_DATA_DIR"] = str(TMP)
os.environ["BC_DATABASE_URL"] = f"sqlite:///{(TMP / 'llm.db').as_posix()}"
os.environ["BC_LLM_PROVIDER"] = "gemini"
os.environ["BC_GEMINI_API_KEY"] = ""
os.environ["BC_OLLAMA_MODEL"] = ""

from app import db, llm, rules
from app.analyze import extract_requirements, score_gap, verify_quote
from app.config import CHUNK_CHARS, CHUNK_OVERLAP
from app.extract import load_pages

db.init_db()

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_tender.pdf"

passed, failed = [], []


def check(name, condition, detail=""):
    (passed if condition else failed).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not condition else ""))


REAL_QUOTE = "The tenderer must hold ISO 27001:2022 certification for information"
FAKE_QUOTE = "Tenderers must provide a notarised apostille from the Vatican embassy"

FAKE_RESPONSE = {
    "requirements": [
        {"page": 2, "quote": REAL_QUOTE,
         "requirement": "ISO 27001 certification is mandatory",
         "kind": "mandatory", "severity": 4},
        {"page": 2, "quote": FAKE_QUOTE,
         "requirement": "Notarised apostille required from the Vatican embassy",
         "kind": "mandatory", "severity": 5},
        {"page": 1, "quote": "The deadline for submission of tenders is 2026-03-20 at 14:00 GMT.",
         "requirement": "Submission deadline", "kind": "administrative", "severity": 2},
    ]
}


class FakeProvider:
    """Stands in for a hosted model. No network, no key."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = 0

    def __call__(self, system, user, json_mode=True, temperature=0.0):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.response


pages = load_pages(str(SAMPLE))
chunks = __import__("app.extract", fromlist=["chunk_pages"]).chunk_pages(
    pages, size=CHUNK_CHARS, overlap=CHUNK_OVERLAP)
scan = rules.scan(pages)

check("chunks produced", len(chunks) > 0, str(len(chunks)))

verified_ok, _, page_ok = verify_quote(REAL_QUOTE, chunks[0], 2)
check("real quote verifies", verified_ok, f"page={page_ok}")

verified_fake, _, _ = verify_quote(FAKE_QUOTE, chunks[0], 2)
check("hallucinated quote rejected", not verified_fake)

verified_trunc, corrected, _ = verify_quote(
    REAL_QUOTE + " and must provide the certificate number", chunks[0], 2)
check("truncated quote accepted and repaired", verified_trunc and "ISO 27001" in corrected)

llm.available = lambda force=False: True
llm.complete = FakeProvider(response=__import__("json").dumps(FAKE_RESPONSE))

items, meta = extract_requirements(chunks, scan)
texts = " ".join(i["requirement"] for i in items)
check("llm path used", meta["engine"] == "llm", str(meta))
check("hallucination counted and blocked", meta["hallucinations_blocked"] >= 1, str(meta))
check("hallucinated requirement absent", "apostille" not in texts.lower(), texts[:200])
check("real requirement present", any("27001" in i["requirement"] for i in items))
check("all surviving items verified", all(i["verified"] for i in items))

llm.complete = FakeProvider(raise_exc=llm.RateLimited("429"))
items2, meta2 = extract_requirements(chunks, scan)
check("rate limit falls back to rules", meta2["engine"] == "rules", str(meta2))
check("rate limit flag reported", meta2.get("rate_limited") is True, str(meta2))
check("fallback still returns requirements", len(items2) > 0, str(len(items2)))

llm.complete = FakeProvider(raise_exc=RuntimeError("boom"))
items3, meta3 = extract_requirements(chunks, scan)
check("hard error falls back to rules", meta3["engine"] == "rules", str(meta3))

llm.complete = FakeProvider(response="this is not json at all")
items4, meta4 = extract_requirements(chunks, scan)
check("unparseable output falls back", meta4["engine"] == "rules", str(meta4))

check("parse_json handles fence", llm.parse_json('```json\n{"a":1}\n```') == {"a": 1})
check("parse_json handles prose", llm.parse_json('Sure! {"a":2} hope that helps') == {"a": 2})
check("parse_json handles garbage", llm.parse_json("no json here") is None)

check("score_gap needs llm", score_gap([{"page": 1, "requirement": "x", "kind": "mandatory",
                                          "severity": 4}], "profile")["available"] is False)
check("score_gap needs profile", score_gap([], "   ")["available"] is False)
check("score_gap handles empty list", score_gap([], "profile")["available"] is False)

VERDICT = {"verdict": "conditional", "confidence": 60,
           "headline": "Two gaps must be closed before submitting.",
           "blockers": [], "next_actions": ["Confirm ISO 27001 scope"],
           "coverage": {"met": 1, "risk": 2, "gap": 0, "unknown": 3}}
llm.available = lambda force=False: True
llm.complete = FakeProvider(response=__import__("json").dumps(VERDICT))
gap = score_gap([{"page": 1, "requirement": "ISO 27001 certification",
                  "kind": "mandatory", "severity": 4, "deadline": None,
                  "threshold": None, "document_required": None}],
                "We are ISO 27001 certified. Turnover 6M.")
check("score_gap works when llm and profile present", gap.get("available") is True, str(gap))
check("score_gap returns parsed verdict", gap.get("verdict") == "conditional", str(gap))

llm.complete = FakeProvider(response="not json")
check("score_gap survives unparseable output", score_gap(
    [{"page": 1, "requirement": "x", "kind": "mandatory", "severity": 4,
      "deadline": None, "threshold": None, "document_required": None}], "p")["available"] is False)

llm.available = lambda force=False: False
status = llm.status()
check("status disabled without key", status["enabled"] is False)
check("status names the missing key", "GEMINI_API_KEY" in status["hint"], status["hint"])
check("status reports requested provider", status["requested_provider"] == "gemini", str(status))

print()
print(f"{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILURES:")
    for name in failed:
        print("  -", name)
import shutil
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if failed else 0)