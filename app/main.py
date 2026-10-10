import os
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, timedelta
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from . import billing, db, llm, report, security
from .analyze import analyze_file
from .config import APP_ENV, SESSION_COOKIE, STATIC_DIR, UPLOAD_DIR
from .db import (
    SESSION_TTL_DAYS,
    SessionLocal,
    SessionRow,
    User,
    is_pro,
    max_pages_for,
    quota_for,
    usage_this_month,
    utcnow,
)

PRODUCTION = APP_ENV == "production"
MAX_UPLOAD_MB = 40
COOKIE_SECURE = os.getenv("BC_COOKIE_SECURE", "1" if PRODUCTION else "0") == "1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="BidGapCheck", version="1.0.0", lifespan=lifespan, docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    with SessionLocal() as s:
        row = s.get(SessionRow, token)
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= utcnow():
            s.delete(row)
            s.commit()
            return None
        user = s.get(User, row.user_id)
        if user is None:
            return None
        return {"id": user.id, "email": user.email, "plan": user.plan,
                "pro": is_pro(user), "expires_at": user.plan_expires_at,
                "customer_id": user.ls_customer_id, "onboarding_done": user.onboarding_done}


def require_user(user: dict | None = Depends(current_user)) -> dict:
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


def usage_payload(user: dict | None) -> dict:
    uid = user["id"] if user else None
    plan = "pro" if user and user["pro"] else "free"
    used = usage_this_month(uid) if uid else 0
    return {
        "plan": plan,
        "quota": quota_for(plan),
        "used": used,
        "max_pages": max_pages_for(plan),
        "unlimited": plan == "pro",
    }


def _run_analysis(aid: str, path: Path, filename: str, profile: str, user_id: str | None, max_pages: int | None) -> None:
    try:
        db.update_analysis(aid, status="running")
        result = analyze_file(str(path), filename, profile=profile, analysis_id=aid, max_pages=max_pages)
        db.update_analysis(aid, status="done", result=result,
                           pages=result["document"]["pages"], finished_at=utcnow(), error=None)
        db.log_event("completed", user_id=user_id, analysis_id=aid, detail=str(result["stats"]))
    except Exception as exc:
        db.update_analysis(aid, status="error", error=str(exc)[:2000], finished_at=utcnow())
        db.log_event("failed", user_id=user_id, analysis_id=aid, detail=traceback.format_exc()[-1500:])


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "env": APP_ENV, "llm": llm.status()}


@app.get("/api/status")
def status(user: dict | None = Depends(current_user)) -> dict:
    return {
        "env": APP_ENV,
        "billing": {
            "configured": billing.configured(),
            "dev": billing.dev_enabled(),
            "prices": {"monthly": billing.PRICE_MONTHLY, "oneoff": billing.PRICE_ONEOFF,
                       "trial": billing.TRIAL_TEXT},
        },
        "user": user,
        "usage": usage_payload(user),
        "llm": llm.status(),
    }


@app.post("/api/auth/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    email = security.normalize_email(email)
    ip = security.client_ip(request)
    wait = security.retry_after(f"signup:{ip}")
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {wait}s.")

    if not security.valid_email(email):
        raise HTTPException(400, "Enter a valid email address.")
    problem = security.password_problem(password)
    if problem:
        raise HTTPException(400, problem)

    with SessionLocal() as s:
        if s.query(User).filter(User.email == email).first():
            raise HTTPException(409, "That email is already registered. Sign in instead.")
        user = User(id=db.new_id(), email=email, password_hash=security.hash_password(password))
        s.add(user)
        s.commit()
        user_id = user.id

    token = security.new_session_token()
    with SessionLocal() as s:
        s.add(SessionRow(token=token, user_id=user_id, expires_at=utcnow() + timedelta(days=SESSION_TTL_DAYS)))
        s.commit()
    db.log_event("signup", user_id=user_id, detail=email)

    response = JSONResponse({"ok": True, "email": email})
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    email = security.normalize_email(email)
    ip = security.client_ip(request)
    key = f"login:{ip}:{email}"
    wait = security.retry_after(key)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {wait}s.")

    with SessionLocal() as s:
        user = s.query(User).filter(User.email == email).first()
        if user is None or not security.verify_password(password, user.password_hash):
            security.register_failure(key)
            db.log_event("login_failed", detail=email)
            raise HTTPException(401, "Wrong email or password.")
        user_id = user.id
    security.clear_failures(key)

    token = security.new_session_token()
    with SessionLocal() as s:
        s.add(SessionRow(token=token, user_id=user_id, expires_at=utcnow() + timedelta(days=SESSION_TTL_DAYS)))
        s.commit()
    db.log_event("login", user_id=user_id)

    response = JSONResponse({"ok": True, "email": email})
    set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with SessionLocal() as s:
            row = s.get(SessionRow, token)
            if row is not None:
                s.delete(row)
                s.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@app.post("/api/auth/password")
def change_password(user: dict = Depends(require_user), current: str = Form(...), new: str = Form(...)):
    problem = security.password_problem(new)
    if problem:
        raise HTTPException(400, problem)
    with SessionLocal() as s:
        row = s.get(User, user["id"])
        if row is None or not security.verify_password(current, row.password_hash):
            raise HTTPException(401, "Current password is wrong.")
        row.password_hash = security.hash_password(new)
        s.commit()
    db.log_event("password_changed", user_id=user["id"])
    return {"ok": True}


@app.delete("/api/account")
def delete_account(user: dict = Depends(require_user)):
    with SessionLocal() as s:
        row = s.get(User, user["id"])
        if row is not None:
            s.delete(row)
            s.commit()
    for row in db.list_analyses(user["id"], limit=1000):
        (UPLOAD_DIR / f"{row['id']}.pdf").unlink(missing_ok=True)
    response = JSONResponse({"ok": True})
    clear_session_cookie(response)
    return response


@app.post("/api/waitlist")
def waitlist(email: str = Form(...), company: str = Form(""), tender: str = Form(""), note: str = Form("")):
    if not security.valid_email(email):
        raise HTTPException(400, "Enter a valid email address.")
    db.add_waitlist(email, company, tender, note)
    return {"ok": True}


@app.post("/api/analyze")
def analyze(background: BackgroundTasks, user: dict = Depends(require_user),
            file: UploadFile = File(...), profile: str = Form(""), profile_name: str = Form("")):
    usage = usage_payload(user)
    if not usage["unlimited"] and usage["used"] >= usage["quota"]:
        raise HTTPException(402, f"Monthly limit reached ({usage['used']}/{usage['quota']}). Upgrade to continue.")

    name = security.safe_filename(file.filename or "document.pdf")
    data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB.")
    if not security.is_pdf(data):
        raise HTTPException(400, "That file is not a readable PDF.")

    if profile_name.strip():
        db.save_profile(user["id"], profile_name.strip(), profile)

    aid = db.new_id()
    path = UPLOAD_DIR / f"{aid}.pdf"
    path.write_bytes(data)
    db.create_analysis(user["id"], aid, name, 0, llm.LLM_PROVIDER)
    background.add_task(_run_analysis, aid, path, name, profile, user["id"], usage["max_pages"])
    return {"id": aid, "status": "queued"}


@app.get("/api/analyses")
def analyses(user: dict = Depends(require_user), limit: int = 50) -> list[dict]:
    return db.list_analyses(user["id"], limit=min(limit, 200))


def _owned(user: dict, aid: str) -> dict:
    row = db.get_analysis(aid, user_id=user["id"], require_owner=True)
    if row is None:
        raise HTTPException(404, "Analysis not found")
    return row


@app.get("/api/analyses/{aid}")
def analysis_detail(aid: str, user: dict = Depends(require_user)) -> dict:
    return _owned(user, aid)


@app.get("/api/analyses/{aid}/report.md")
def report_md(aid: str, user: dict = Depends(require_user)) -> PlainTextResponse:
    row = _owned(user, aid)
    if row["status"] != "done":
        raise HTTPException(404, "Report not ready")
    return PlainTextResponse(report.to_markdown(row["result"]))


@app.get("/api/analyses/{aid}/report.html")
def report_html(aid: str, user: dict = Depends(require_user)) -> HTMLResponse:
    row = _owned(user, aid)
    if row["status"] != "done":
        raise HTTPException(404, "Report not ready")
    return HTMLResponse(report.to_html(row["result"]))


@app.get("/api/analyses/{aid}/source.pdf")
def source_pdf(aid: str, user: dict = Depends(require_user)) -> FileResponse:
    _owned(user, aid)
    path = UPLOAD_DIR / f"{aid}.pdf"
    if not path.exists():
        raise HTTPException(404, "Source file no longer stored")
    return FileResponse(path, media_type="application/pdf")


@app.get("/api/profiles")
def profiles(user: dict = Depends(require_user)) -> list[dict]:
    return db.list_profiles(user["id"])


@app.post("/api/profiles")
def save_profile(name: str = Form(...), body: str = Form(...),
                 user: dict = Depends(require_user)) -> dict:
    name, body = name.strip(), body.strip()
    if not name or not body:
        raise HTTPException(400, "name and body are required")
    return {"id": db.save_profile(user["id"], name, body), "name": name}


@app.get("/api/profiles/{name}")
def profile(name: str, user: dict = Depends(require_user)) -> dict:
    row = db.get_profile(user["id"], name)
    if row is None:
        raise HTTPException(404, "Profile not found")
    return row


@app.delete("/api/profiles/{name}")
def remove_profile(name: str, user: dict = Depends(require_user)) -> dict:
    return {"deleted": db.delete_profile(user["id"], name)}


@app.post("/api/billing/checkout")
def checkout(plan_key: str = Form(...), user: dict = Depends(require_user)) -> dict:
    try:
        url = billing.create_checkout(user["id"], user["email"], plan_key)
    except billing.BillingNotConfigured as exc:
        raise HTTPException(503, str(exc))
    return {"url": url}


@app.post("/api/billing/webhook")
async def webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Signature", "")
    if not billing.verify_signature(raw, signature):
        db.log_event("webhook_bad_signature", detail=request.headers.get("X-Event-Name", ""))
        raise HTTPException(401, "Invalid signature")
    import json as _json

    try:
        payload = _json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON")
    return billing.handle_event(payload)


@app.post("/api/billing/dev-upgrade")
def dev_upgrade(user: dict = Depends(require_user)):
    if not billing.dev_enabled():
        raise HTTPException(404, "Not found")
    db.grant_plan(user["id"], "pro", months=1)
    return {"ok": True, "plan": "pro"}


@app.get("/app")
def app_page(request: Request, user: dict | None = Depends(current_user)):
    if user is None:
        return RedirectResponse("/login?next=/app", status_code=302)
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/")
def landing():
    return FileResponse(STATIC_DIR / "landing.html")


@app.get("/pricing")
def pricing():
    return FileResponse(STATIC_DIR / "pricing.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/signup")
def signup_page():
    return FileResponse(STATIC_DIR / "signup.html")


@app.get("/legal/terms")
def terms():
    return FileResponse(STATIC_DIR / "legal_terms.html")


@app.get("/legal/privacy")
def privacy():
    return FileResponse(STATIC_DIR / "legal_privacy.html")


def request_base() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


@app.get("/robots.txt")
def robots():
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nSitemap: " + request_base() + "/sitemap.xml\n")


@app.get("/sitemap.xml")
def sitemap():
    base = request_base()
    urls = ["", "/pricing", "/signup", "/legal/terms", "/legal/privacy"]
    body = "".join(
        f"<url><loc>{base}/{u.lstrip('/') if u else ''}</loc><changefreq>monthly</changefreq></url>"
        for u in urls)
    return PlainTextResponse(f'<?xml version="1.0" encoding="UTF-8"?>'
                             f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                             media_type="application/xml")