import html
import time

VERDICT_LABEL = {
    "bid": "BID",
    "conditional": "CONDITIONAL - resolve unknowns before submitting",
    "no_bid": "NO BID - blockers found",
}

VERDICT_COLOR = {"bid": "#0f7b46", "conditional": "#9a6400", "no_bid": "#a11b1b"}

CATEGORY_LABEL = {
    "bid_security": "Bid bond / guarantee",
    "insurance": "Insurance",
    "turnover": "Turnover threshold",
    "experience": "Experience requirement",
    "personnel": "Key personnel / certification",
    "litigation": "Litigation / financial standing",
    "subcontracting": "Subcontracting controls",
    "language": "Language & translation",
    "pricing": "Pricing schedule",
    "format": "Submission format",
}

SEVERITY_LABEL = {5: "Disqualifying", 4: "Hard gate", 3: "Scored", 2: "Administrative", 1: "Minor"}


def to_markdown(result: dict) -> str:
    doc = result.get("document", {})
    engine = result.get("engine", {})
    verdict = result.get("verdict", {})
    lines = [
        f"# Tender Risk Review - {doc.get('filename', 'document')}",
        "",
        (f"Generated {time.strftime('%Y-%m-%d %H:%M')} | {doc.get('pages', 0)} pages | "
         f"engine: {engine.get('engine', 'rules')}"
         f"{' + ' + engine.get('model', '') if engine.get('llm') else ' (no LLM)'}"),
        "",
    ]

    if verdict.get("available"):
        v = verdict.get("verdict", "conditional")
        lines += [f"## Verdict: {VERDICT_LABEL.get(v, v.upper())}", ""]
        if verdict.get("headline"):
            lines += [verdict["headline"], ""]
        if verdict.get("coverage"):
            cov = verdict["coverage"]
            lines += [
                (f"Coverage: met {cov.get('met', 0)} | at risk {cov.get('risk', 0)} | "
                 f"gap {cov.get('gap', 0)} | unknown {cov.get('unknown', 0)} | "
                 f"confidence {verdict.get('confidence', 0)}%"),
                "",
            ]
        if verdict.get("blockers"):
            lines += ["### Blockers", ""]
            for b in verdict["blockers"]:
                lines.append(f"- **p.{b.get('page', 0)}** {b.get('requirement')} - {b.get('why')}")
            lines.append("")
        if verdict.get("next_actions"):
            lines += ["### Next actions", ""]
            lines += [f"{i}. {a}" for i, a in enumerate(verdict.get("next_actions", []), 1)]
            lines.append("")

    deadlines = result.get("deadlines", [])
    if deadlines:
        lines += ["## Deadlines", "", "| Page | Date(s) | Context |", "|---|---|---|"]
        for d in deadlines[:15]:
            ctx = d.get("sentence", "").replace("|", "/")[:200]
            lines.append(f"| {d.get('page')} | {', '.join(d.get('dates', []))} | {ctx} |")
        lines.append("")

    disq = result.get("requirements", [])
    mandatory = [r for r in disq if r.get("severity", 0) >= 4]
    if mandatory:
        lines += ["## Hard gates and disqualifying clauses", "",
                  "| Page | Severity | Requirement |", "|---|---|---|"]
        for r in mandatory[:40]:
            req = r.get("requirement", "").replace("|", "/")[:260]
            lines.append(f"| {r.get('page')} | {SEVERITY_LABEL.get(r['severity'], r['severity'])} | {req} |")
        lines.append("")

    docs_needed = [r for r in disq if r.get("document_required")]
    if docs_needed:
        lines += ["## Documents to supply", ""]
        for r in docs_needed[:40]:
            lines.append(f"- **p.{r.get('page')}** {r['document_required']}")
        lines.append("")

    checks = result.get("special_checks", [])
    if checks:
        lines += ["## Compliance checkpoints", "", "| Page | Area | Evidence in document |", "|---|---|---|"]
        for c in checks:
            lines.append(f"| {c.get('page')} | {CATEGORY_LABEL.get(c.get('check'), c.get('check'))} | "
                         f"{c.get('matched', '')} |")
        lines.append("")

    money = result.get("financial", [])
    if money:
        lines += ["## Monetary thresholds", ""]
        for m in money[:15]:
            lines.append(f"- **p.{m.get('page')}** {m.get('sentence', '')[:260]}")
        lines.append("")

    stats = result.get("stats", {})
    lines += [
        "## Method",
        "",
        ("Every line above is extracted from the document and carries a page number. "
         "Quotes produced by the model were verified character-by-character against the source PDF; "
         "unverifiable output was discarded rather than shown."),
        "",
        f"- Requirements extracted: {stats.get('requirements', 0)}",
        f"- Mandatory: {stats.get('mandatory', 0)}",
        f"- Quote verification rate: {engine.get('verified_ratio', 1.0)}",
        "",
        "This review assists a human decision. It does not replace legal or procurement review.",
    ]
    return "\n".join(lines)


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def to_html(result: dict) -> str:
    doc = result.get("document", {})
    engine = result.get("engine", {})
    verdict = result.get("verdict", {})
    e = _esc

    verdict_block = ""
    if verdict.get("available"):
        v = verdict.get("verdict", "conditional")
        color = VERDICT_COLOR.get(v, "#555")
        cov = verdict.get("coverage", {}) if isinstance(verdict.get("coverage"), dict) else {}
        blockers = "".join(
            f"<li><b>p.{e(b.get('page'))}</b> {e(b.get('requirement'))}<br><span class=muted>{e(b.get('why'))}</span></li>"
            for b in verdict.get("blockers", [])
        )
        actions = "".join(f"<li>{e(a)}</li>" for a in verdict.get("next_actions", []))
        verdict_block = f"""
        <div class="verdict" style="border-left-color:{color}">
          <div class="verdict-label" style="color:{color}">{e(VERDICT_LABEL.get(v, v.upper()))}</div>
          <p class="headline">{e(verdict.get('headline'))}</p>
          <div class="coverage">
            <span>Met <b>{e(cov.get('met', 0))}</b></span>
            <span>At risk <b>{e(cov.get('risk', 0))}</b></span>
            <span>Gap <b>{e(cov.get('gap', 0))}</b></span>
            <span>Unknown <b>{e(cov.get('unknown', 0))}</b></span>
            <span>Confidence <b>{e(verdict.get('confidence', 0))}%</b></span>
          </div>
          {f'<h3>Blockers</h3><ul>{blockers}</ul>' if blockers else ''}
          {f'<h3>Next actions</h3><ol>{actions}</ol>' if actions else ''}
        </div>"""

    def rows(items, getter, cls=""):
        return "".join(f"<tr class='{cls}'>{getter(i)}</tr>" for i in items)

    deadline_rows = "".join(
        f"<tr><td>{e(d.get('page'))}</td><td class=date>{e(', '.join(d.get('dates', [])))}</td>"
        f"<td>{e(d.get('sentence', '')[:300])}</td></tr>"
        for d in result.get("deadlines", [])[:15])

    gates = [r for r in result.get("requirements", []) if r.get("severity", 0) >= 4]
    gate_rows = "".join(
        f"<tr><td>{e(r.get('page'))}</td>"
        f"<td><span class='sev sev{r.get('severity')}'>{e(SEVERITY_LABEL.get(r.get('severity')))}</span></td>"
        f"<td>{e(r.get('requirement', ''))}</td></tr>"
        for r in gates[:40])

    check_rows = "".join(
        f"<tr><td>{e(c.get('page'))}</td>"
        f"<td>{e(CATEGORY_LABEL.get(c.get('check'), c.get('check')))}</td>"
        f"<td>{e(c.get('matched'))}</td></tr>"
        for c in result.get("special_checks", []))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tender Risk Review - {e(doc.get('filename'))}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         max-width:960px; margin:0 auto; padding:40px 24px; color:#1a1a1a; background:#fff; }}
  h1 {{ font-size:24px; margin:0 0 6px; }}
  h2 {{ font-size:18px; margin:32px 0 10px; padding-bottom:6px; border-bottom:1px solid #e5e5e5; }}
  h3 {{ font-size:15px; margin:20px 0 8px; }}
  .meta {{ color:#666; font-size:13px; margin-bottom:24px; }}
  .verdict {{ border-left:5px solid #999; background:#fafafa; padding:18px 20px; border-radius:4px; }}
  .verdict-label {{ font-weight:700; font-size:20px; letter-spacing:.5px; }}
  .headline {{ font-size:16px; margin:8px 0 12px; }}
  .coverage {{ display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:#444; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:9px 10px; border-bottom:1px solid #eee; vertical-align:top; }}
  th {{ background:#f7f7f7; font-size:12px; text-transform:uppercase; letter-spacing:.4px; color:#555; }}
  td.date {{ white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .sev {{ display:inline-block; padding:2px 7px; border-radius:3px; font-size:11px;
          font-weight:600; text-transform:uppercase; }}
  .sev5 {{ background:#a11b1b; color:#fff; }} .sev4 {{ background:#d97706; color:#fff; }}
  .sev3 {{ background:#eef2f7; color:#335; }} .sev2 {{ background:#f3f3f3; color:#666; }}
  .muted {{ color:#777; font-size:13px; }}
  ul, ol {{ padding-left:20px; }} li {{ margin-bottom:6px; }}
  .method {{ margin-top:36px; padding-top:14px; border-top:1px solid #e5e5e5;
             font-size:13px; color:#666; }}
</style></head><body>
<h1>Tender Risk Review</h1>
<div class="meta">{e(doc.get('filename'))} &middot; {e(doc.get('pages', 0))} pages &middot;
generated {time.strftime('%Y-%m-%d %H:%M')} &middot;
engine: {e(engine.get('engine', 'rules'))}{' + ' + e(engine.get('model', '')) if engine.get('llm') else ' (no LLM)'}</div>
{verdict_block}
{f'<h2>Deadlines</h2><table><tr><th>Page</th><th>Date</th><th>Context</th></tr>{deadline_rows}</table>' if deadline_rows else ''}
{f'<h2>Hard gates and disqualifying clauses</h2><table><tr><th>Page</th><th>Severity</th><th>Requirement</th></tr>{gate_rows}</table>' if gate_rows else ''}
{f'<h2>Compliance checkpoints</h2><table><tr><th>Page</th><th>Area</th><th>Evidence found</th></tr>{check_rows}</table>' if check_rows else ''}
<div class="method">
Every line above is extracted from the document and carries a page number.
Model-generated quotes were verified against the source PDF; unverifiable output was discarded.
This review assists a human decision and does not replace legal or procurement review.
</div>
</body></html>"""