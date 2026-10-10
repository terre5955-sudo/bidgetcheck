# BidGapCheck

Reads a tender document, extracts every mandatory requirement and every disqualifying
clause, and produces a report where each line carries a page number and the verbatim
sentence from the source PDF.

Multi-tenant, deployable, payments wired. Runs on a free hosting tier with a free Postgres.

## What it does not do

- Scanned PDFs with no text layer. OCR is not implemented and is not planned.
- It does not tell you whether a clause is legally enforceable. It tells you what the
  document says and where.

## Local development

```powershell
.\run.ps1
```

Then open http://127.0.0.1:8000

To test the payment path without a payment account, start with:

```powershell
$env:BC_BILLING_DEV="1"; .\run.ps1
```

The Pro button then grants access locally instead of redirecting to a checkout.

Generate a sample document:

```powershell
.\.venv\Scripts\python.exe samples\make_sample_pdf.py
```

Run the tests (49 checks: auth, quotas, tenant isolation, billing signature, extraction):

```powershell
.\.venv\Scripts\python.exe tests\smoke.py
```

## Deploy

Free hosting is the source of most of the constraints below. Verified against Render's
own documentation, September 2026.

**The three facts that shape the architecture:**

1. A free web service **sleeps after 15 minutes without traffic** and takes about a minute
   to wake. A visitor arriving at a cold site waits on a loading page.
2. A free service has **no persistent disk**. Everything written to the filesystem is lost
   on every sleep, restart, or redeploy.
3. A free Render Postgres is **deleted 30 days after creation**, after a 14-day grace
   period. An expired database is inaccessible, and after the grace period it is gone.

**What the code does about each:**

1. `BC_LLM_MAX_CALLS` caps model calls per document, and `BC_LLM_DELAY_MS` paces them, so an
   analysis finishes in well under a minute instead of running for as long as the model needs.
   If a job is still unfinished after `BC_STALE_ANALYSIS_SECONDS`, startup reaps it and the
   user is told the server restarted, rather than watching a spinner forever.
2. Uploaded PDFs are written to a temporary directory, analysed, then deleted in a `finally`
   block. Reports and company profiles live in Postgres and survive. `/source.pdf` returns
   410 with an explanation rather than pretending.
3. `render.yaml` declares **no** `databases:` block and **no** `disk:` block, both
   deliberately. Supply `BC_DATABASE_URL` from an external provider.

**Recommended free setup, no card required:**

| Need | Service | Why not the obvious choice |
|---|---|---|
| Hosting | Render free web service | Sleeps after 15 min. Acceptable while there is no traffic. |
| Database | Neon free Postgres | Render's free database is deleted after 30 days. Neon does not expire. |
| AI | Gemini free tier | No card, native JSON mode. |
| Payments | Lemon Squeezy | Merchant of Record, handles VAT across the EU. |

**To deploy:**

1. Create a free Postgres project at neon.com and copy its pooled connection string. It
   starts with `postgresql://`, change the prefix to `postgresql+psycopg://`.
2. Push this repository, then on render.com choose **New → Blueprint** and select it.
3. Fill in the five values Render asks for: `BC_DATABASE_URL`, `BC_GEMINI_API_KEY`, and the
   four `LS_*` payment values.
4. Change `PUBLIC_BASE_URL` in `render.yaml` to your own domain before you rely on it in
   legal text and the sitemap.

The blueprint declares only the web service. There is no database and no disk, on purpose.

**The honest cost of free hosting.** A customer who arrives at a cold site waits about a
minute before the page loads. Free tiers of AI models run roughly 30 requests per minute, so
documents longer than about eight chunks are partly handled by the deterministic engine. This
is fine to validate the idea and not fine to sell. Once money arrives, hosting is $7 a month
and that is not the expensive part.

## Payments

Lemon Squeezy, not Stripe. Stripe is not directly available for Russian residents, and
Lemon Squeezy acts as Merchant of Record: it takes the sale, charges and remits VAT or sales
tax to every country you sell into, and pays you net. You do not register for tax in each
jurisdiction.

Point the webhook at `POST /api/billing/webhook` and paste the signing secret. The handler
verifies the HMAC signature and ignores any event carrying a different store ID.

Handled events: `order_created`, `subscription_created`, `subscription_updated`,
`subscription_resumed` grant access. `subscription_cancelled` and `subscription_paused` set
the expiry to the subscription's `ends_at`, so a customer who cancels keeps access until the
period they already paid for runs out. `subscription_expired`, `subscription_unpaid` and
`refund_created` revoke immediately.

## Plans

Enforced in `app/main.py`, values in the environment.

| | Free | Pro |
|---|---|---|
| Documents per month | 3 | 150 |
| Pages per document | 80 | no practical cap |
| Bid / no-bid verdict | no | yes |
| Saved company profiles | yes | yes |

## Two engines, and why the deterministic one still matters

The model reads the document. Every sentence it produces is checked back against the PDF and
discarded if it cannot be found, so the model is never allowed to write the report.

Underneath sits a deterministic engine in `app/rules.py` with no external dependency. It finds
deadlines, disqualification language, mandatory clauses, enumerated document lists, monetary
thresholds, and eleven compliance areas. On the sample tender it returns 4 deadlines and 26
requirements, all page-cited, with no model involved.

It is not a fallback for nostalgia. It is what runs when the model provider is down, when the
free quota is exhausted, or when the provider rejects the request. Free tiers are rate limited
to roughly 30 requests per minute and a few thousand tokens per minute, so a long tender will
hit the wall. When it does, the report is still produced and the engine field says which path
ran. Never advertise a model you might silently stop using.

## Providers

Set `BC_LLM_PROVIDER` to one of:

| Provider | Setup | Notes |
|---|---|---|
| `gemini` | `BC_GEMINI_API_KEY` | Default. Free tier, native JSON mode. |
| `groq` | `BC_GROQ_API_KEY` | OpenAI-compatible, free tier, very fast. |
| `ollama` | run Ollama locally | Free but only on your own machine. |
| `openai` | `BC_OPENAI_API_KEY`, `BC_OPENAI_BASE_URL` | Also covers OpenRouter and Together. |

`BC_LLM_DELAY_MS` spaces requests out. Free tiers allow around 30 requests per minute, so 2000
is the safe setting in production.

Free-tier limits change often and the documentation is not always consistent with the API.
The reliable check is the API itself: `available()` calls the models endpoint and returns
false if the key is missing or rejected, and `/api/status` surfaces that to the interface.

### Fixing a critical bug this uncovered

`score_gap` called `.strip()` on a list. With no model configured the function returned before
reaching that line, so 49 tests passed and the bug shipped. The moment a real model was
connected and a user supplied a company profile, every analysis would have failed with an
unhandled exception. It is fixed and covered by four tests in `tests/llm_test.py`.

## The part that is not standard

Every sentence the model produces is checked against the source PDF character by character,
using `difflib` against the sentences of the cited page. If it cannot be located, it is
deleted rather than displayed. Exact substring match first, fuzzy sentence match above 0.72
similarity as a fallback, everything else discarded.

This is not a detail. Generic AI invents clause text, and a bid manager discovers it during
submission, which destroys any trust in the report. Deleting unverifiable output is what
makes the report usable in a real procurement process.

## Security decisions worth knowing

- Passwords hashed with scrypt, N=16384, r=8, p=1, 16-byte random salt, constant-time
  comparison.
- Session token in an `httponly`, `samesite=lax` cookie. `secure` is forced on in production.
- Login and signup are rate limited per IP and per email, with exponential lockout.
- Every analysis read is scoped to its owner. One user cannot read another's report or source
  PDF. Covered by three tests.
- Uploads are validated by PDF magic bytes, not by the filename or the declared MIME type.
  Cap is 40 MB.
- The dev payment bypass is force-disabled when `BC_ENV=production`, not merely left off.

## Layout

```
app/
  main.py        routes, auth, quotas, job lifecycle
  db.py          SQLAlchemy models, SQLite and Postgres
  security.py    password hashing, sessions, rate limiting, upload validation
  billing.py     Lemon Squeezy checkout and signed webhooks
  analyze.py     pipeline: chunk -> extract -> verify -> merge -> score
  rules.py       deterministic extraction, the always-on engine
  extract.py     PDF text layer to pages, and chunking
  llm.py         provider abstraction, JSON parsing
  report.py      Markdown and standalone HTML reports
  static/        landing, pricing, auth, app, legal pages
prompts/        extraction and verdict contracts for the model
tests/smoke.py   49 checks covering the whole flow
sales/           offer, outreach, discovery questions, landing copy
samples/         generator for a synthetic 3-page tender
```

## Before taking money

1. Fill in every `[FILL IN: ...]` in `app/static/legal_terms.html` and
   `app/static/legal_privacy.html`. They are drafts, not legal advice. Have a lawyer read them.
2. Get the Lemon Squeezy account approved. They require the policies to be live on a domain you
   control, with matching contact details. This takes days, so start it early.
3. Put your own domain in front of it. A `onrender.com` address will lose deals you have already
   paid for in effort.
4. Fix the contact addresses. `pricing.html` and `landing.html` currently point at
   `hello@example.com`.