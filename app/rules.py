import re
from dataclasses import dataclass

from .extract import Page

MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)

DEADLINE_CONTEXT = re.compile(
    r"\b(deadline|closing date|closing time|submission date|submit by|due by|due date|"
    r"no later than|not later than|remain valid|valid and irrevocable|validity period|"
    r"bid validity|price validity|questions? (?:due|deadline|must be submitted)|"
    r"clarification (?:deadline|date|questions)|clarification questions|"
    r"proposal due|tender due|bids? (?:are |must be |shall be )?(?:due|submitted|received)|"
    r"closing of (?:the )?(?:tender|bids)|drawings? (?:are |must be )?(?:available|issued)|"
    r"bid validity period)",
    re.IGNORECASE,
)

DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{MONTHS})\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"),
    re.compile(r"\b\d{1,2}\s*[:.]\d{2}\b"),
    re.compile(r"\b\d{1,3}\s+(?:hours?|working days?|business days?|calendar days?|days?|weeks?|months?)\s+"
               r"(?:from|after|following)\b", re.IGNORECASE),
]

OBLIGATION = re.compile(
    r"\b(must|shall|mandator(?:y|ily)|is required to|are required to|is obliged to|"
    r"needs? to|has to|have to|will be required|failure to comply)\b",
    re.IGNORECASE,
)

STRONG_DISQUALIFIER = re.compile(
    r"(disqualif\w*|ineligib\w*|will be rejected|shall be rejected|rejected as|"
    r"will be treated as|rendered (?:ineligible|non-compliant)|non-?responsive|"
    r"excluded from (?:the )?evaluation|grounds? for exclusion|excluded on grounds|"
    r"treated as incomplete|without further notice|blacklist\w*|debarr?ed)",
    re.IGNORECASE,
)

WEAK_DISQUALIFIER = re.compile(
    r"(may be rejected|may be excluded|may result in (?:rejection|exclusion)|"
    r"could lead to rejection|subject to rejection|reserve the right to reject)",
    re.IGNORECASE,
)

DOCUMENT_NOUN = re.compile(
    r"\b(form|certificate|document|statement|schedule|appendix|annex|licen[cs]e|insurance|"
    r"bond|guarantee|reference|financial|audit|bank statement|plan|methodology|"
    r"affidavit|power of attorney|deed|tax (?:certificate|clearance)|registration|"
    r"passport|photograph|resume|curriculum vitae|cv|organisational chart|"
    r"track record|experience|portfolio|technical (?:proposal|offer)|commercial (?:offer|proposal)|"
    r"declaration|undertaking|memorandum|mou|list|table|drawings?|bill of quantities|"
    r"price schedule|pricing schedule|tender document|questionnaire)\b",
    re.IGNORECASE,
)

SUBMIT_VERB = re.compile(
    r"\b(submit|attach|provide|include|enclose|deposit|upload|present|furnish|"
    r"accompany|appended|endorsed|complete|fill in)\b",
    re.IGNORECASE,
)

LIST_INTRO = re.compile(
    r"\b(must|shall|should)\s+(?:\w+\s+){0,3}?(submit|attach|provide|include|supply|furnish|"
    r"present|deposit)\b.{0,80}?\b(following|as follows|listed below|set out below|below)\b",
    re.IGNORECASE,
)

ENUM_ITEM = re.compile(r"^\s*\(([a-z]|[ivx]{1,4}|\d{1,2})\)\s+(\S.{5,400}?)\s*$", re.IGNORECASE | re.MULTILINE)

MONEY = re.compile(
    r"((?:USD|EUR|GBP|US\$|€|£|\$|RMB|INR|AED)\s?[\d,]{3,}(?:\.\d+)?)|"
    r"(\b\d[\d,]{5,}(?:\.\d+)?\s*(?:USD|EUR|GBP|dollars?|euros?|pounds?|usd|eur)\b)",
    re.IGNORECASE,
)

MINOR = re.compile(r"\b(not less than|no less than|minimum of|at least|at a minimum|"
                   r"not fewer than|no fewer than|of not less than)\b", re.IGNORECASE)

CHECKS: list[tuple[str, re.Pattern, int]] = [
    ("bid_security", re.compile(
        r"\b(bid (?:bond|security|guarantee)|performance (?:bond|guarantee|security)|"
        r"advance payment guarantee|retention (?:bond|money|guarantee)|bank guarantee)\b", re.IGNORECASE), 5),
    ("insurance", re.compile(
        r"\b(public liability|professional indemnity|indemnity insurance|"
        r"workers(?:'|’)? compensation|employer(?:'|’)?s liability|"
        r"insurance certificate|insurance policy)\b", re.IGNORECASE), 4),
    ("turnover", re.compile(
        r"\b(annual turnover|average annual turnover|turnover of|financial turnover|"
        r"revenue of|minimum turnover|annual revenue)\b", re.IGNORECASE), 4),
    ("certification", re.compile(
        r"\b(iso\s?\d{4,5}(?::\d{4})?|certification|certificate number|accredited|"
        r"certified (?:manager|professional|specialist)|licence|licenses|licenses)\b", re.IGNORECASE), 4),
    ("litigation", re.compile(
        r"\b(litigation|legal proceedings|contingent liabilities|pending claims|"
        r"insolvency|bankruptcy|declaration of solvency)\b", re.IGNORECASE), 5),
    ("personnel", re.compile(
        r"\b(key personnel|named personnel|project manager|project director|team leader|"
        r"cv of|curriculum vitae)\b", re.IGNORECASE), 3),
    ("experience", re.compile(
        r"\b(\d+\s*\+?\s*years?(?:'|’)?s?\b.{0,40}?\bexperience)|"
        r"\b(years? of experience)|similar contracts?\b|track record\b", re.IGNORECASE), 3),
    ("subcontracting", re.compile(
        r"\b(subcontract\w*|sub-?contracting|named subcontractor|subcontractor list|"
        r"consent of (?:the )?(?:client|authority|employer))\b", re.IGNORECASE), 3),
    ("language", re.compile(
        r"\b(english version|translated into|certified english translation|"
        r"in english|documents? (?:must|shall|should) be in)\b", re.IGNORECASE), 2),
    ("pricing", re.compile(
        r"\b(price schedule|bill of quantities|\bboq\b|pricing schedule|unit rates?|"
        r"fixed price|lump sum|schedule of rates|firm price|excluding vat)\b", re.IGNORECASE), 3),
    ("format", re.compile(
        r"\b(file format|pdf format|electronic submission|e-?procurement|portal|"
        r"upload via|soft copy|hard copy|two envelopes|sealed envelope|sealed tender|"
        r"font size|page limit|maximum \d+ pages|exceeding \d+ pages)\b", re.IGNORECASE), 3),
]

CHECK_PATTERNS = {key: pattern for key, pattern, _ in CHECKS}

CHECK_LABELS = {
    "bid_security": "Bid bond / guarantee", "insurance": "Insurance",
    "turnover": "Turnover threshold", "certification": "Certification / licence",
    "experience": "Experience requirement", "personnel": "Key personnel",
    "litigation": "Litigation / solvency", "subcontracting": "Subcontracting controls",
    "language": "Language & translation", "pricing": "Pricing schedule",
    "format": "Submission format",
}

CATEGORY_LABEL = CHECK_LABELS

SEVERITY_LABEL = {5: "Disqualifying", 4: "Hard gate", 3: "Scored", 2: "Administrative", 1: "Minor"}


@dataclass
class Hit:
    page: int
    sentence: str
    category: str
    severity: int

    def as_dict(self) -> dict:
        return {"page": self.page, "sentence": self.sentence, "category": self.category,
                "severity": self.severity, "source": "rules"}


SENTENCE_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-Z0-9\"'(\[])")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s*\n\s*", " ", text)
    return [p for p in (s.strip() for s in SENTENCE_SPLIT.split(text)) if len(p) >= 25]


def matched_checks(sentence: str) -> list[tuple[str, int]]:
    return [(key, severity) for key, pattern, severity in CHECKS if pattern.search(sentence)]


def _enumerate_items(page: Page) -> list[str]:
    raw = page.text
    items = []
    for _, body in ENUM_ITEM.findall(raw):
        body = re.sub(r"\s+", " ", body).strip()
        if 10 <= len(body) <= 400:
            items.append(body)
    return items


def scan(pages: list[Page], limit: int = 140) -> dict:
    deadlines: list[dict] = []
    strong: list[Hit] = []
    weak: list[Hit] = []
    docs: list[Hit] = []
    obligations: list[Hit] = []
    money: list[Hit] = []
    checks: list[dict] = []
    enumerated: list[Hit] = []
    ranked: list[tuple[int, int, str]] = []

    for page in pages:
        if not page.text:
            continue

        page_has_list_intro = bool(LIST_INTRO.search(page.text))
        if page_has_list_intro:
            for body in _enumerate_items(page):
                enumerated.append(Hit(page.number, body, "enumerated_requirement", 3))

        for sentence in split_sentences(page.text):
            dates: list[str] = []
            for pattern in DATE_PATTERNS:
                dates.extend(m.group(0) for m in pattern.finditer(sentence))
            if dates and DEADLINE_CONTEXT.search(sentence):
                deadlines.append({"page": page.number, "sentence": sentence[:900],
                                  "dates": sorted(set(dates))})

            if STRONG_DISQUALIFIER.search(sentence):
                strong.append(Hit(page.number, sentence[:900], "disqualification", 5))
            elif WEAK_DISQUALIFIER.search(sentence):
                weak.append(Hit(page.number, sentence[:900], "rejection_risk", 3))

            hits = matched_checks(sentence)
            for key, _severity in hits:
                matched = CHECK_PATTERNS[key].search(sentence)
                checks.append({"page": page.number, "check": key,
                               "matched": matched.group(0) if matched else key,
                               "sentence": sentence[:900]})

            if OBLIGATION.search(sentence) and SUBMIT_VERB.search(sentence) and DOCUMENT_NOUN.search(sentence):
                docs.append(Hit(page.number, sentence[:900], "required_document", 3))

            if OBLIGATION.search(sentence) and hits:
                severity = max(s for _, s in hits)
                if MINOR.search(sentence) and MONEY.search(sentence):
                    severity = min(5, severity + 1)
                obligations.append(Hit(page.number, sentence[:900], "mandatory_requirement", severity))

            if MONEY.search(sentence) and re.search(
                    r"\b(bond|guarantee|insurance|turnover|fee|value|price|penalt|threshold|liquid)", sentence, re.IGNORECASE):
                money.append(Hit(page.number, sentence[:900], "financial", 3))

            score = 0
            if STRONG_DISQUALIFIER.search(sentence):
                score += 30
            elif WEAK_DISQUALIFIER.search(sentence):
                score += 14
            if OBLIGATION.search(sentence):
                score += 10
            if DEADLINE_CONTEXT.search(sentence):
                score += 12
            if SUBMIT_VERB.search(sentence):
                score += 5
            if DOCUMENT_NOUN.search(sentence):
                score += 4
            if hits:
                score += 6
            if len(sentence) > 700:
                score -= 10
            if score >= 20:
                ranked.append((score, page.number, sentence[:900]))

    ranked.sort(key=lambda x: -x[0])

    return {
        "deadlines": _uniq_dicts(deadlines)[:30],
        "disqualifiers": [h.as_dict() for h in _uniq(strong)[:40]],
        "rejection_risks": [h.as_dict() for h in _uniq(weak)[:30]],
        "required_documents": [h.as_dict() for h in _uniq(docs)[:40]],
        "enumerated": [h.as_dict() for h in _uniq(enumerated)[:60]],
        "obligations": [h.as_dict() for h in _uniq(obligations)[:60]],
        "financial": [h.as_dict() for h in _uniq(money)[:25]],
        "special_checks": _uniq_checks(checks),
        "ranked_sentences": [{"page": p, "sentence": s, "score": sc} for sc, p, s in ranked[:limit]],
        "stats": {
            "pages_scanned": len(pages),
            "deadlines_found": len(_uniq_dicts(deadlines)),
            "disqualifiers_found": len(_uniq(strong)),
            "documents_found": len(_uniq(docs)),
            "enumerated_found": len(_uniq(enumerated)),
            "obligations_found": len(_uniq(obligations)),
            "special_checks_found": len(checks),
        },
    }


def _uniq(items: list[Hit]) -> list[Hit]:
    seen: set[str] = set()
    out: list[Hit] = []
    for hit in items:
        key = re.sub(r"\W+", "", hit.sentence.lower())[:150]
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def _uniq_dicts(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = "|".join(item.get("dates", [])) + re.sub(r"\W+", "", item["sentence"].lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _uniq_checks(items: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        key = item["check"]
        if key not in best:
            best[key] = item
            order.append(key)
        elif len(item["matched"]) > len(best[key]["matched"]):
            best[key] = item
    return [best[k] for k in order]