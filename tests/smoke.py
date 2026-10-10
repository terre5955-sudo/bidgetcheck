import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = Path(tempfile.mkdtemp(prefix="bgc_test_"))
os.environ["BC_DATA_DIR"] = str(TMP)
os.environ["BC_DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ["BC_BILLING_DEV"] = "1"
os.environ["BC_ENV"] = "development"
os.environ["BC_COOKIE_SECURE"] = "0"
os.environ["BC_FREE_QUOTA"] = "2"
os.environ["BC_FREE_MAX_PAGES"] = "80"
os.environ["BC_PRO_QUOTA"] = "50"

from fastapi.testclient import TestClient

from app.main import app

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_tender.pdf"

passed, failed = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (passed if condition else failed).append(name)
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  -> {detail}" if detail and not condition else ""))


def wait(client, aid, tries=40):
    for _ in range(tries):
        row = client.get(f"/api/analyses/{aid}").json()
        if row["status"] in ("done", "error"):
            return row
    return row


def _password_roundtrip():
    from app import security
    stored = security.hash_password("correcthorse1")
    return (security.verify_password("correcthorse1", stored)
            and not security.verify_password("correcthorse2", stored))


with TestClient(app) as client:
    check("health", client.get("/api/health").json()["ok"] is True)
    for path in ("/", "/pricing", "/login", "/signup", "/legal/terms", "/legal/privacy", "/robots.txt", "/sitemap.xml"):
        r = client.get(path)
        check(f"public page {path}", r.status_code == 200, f"status={r.status_code}")

    check("app requires auth", client.get("/app", follow_redirects=False).status_code == 302)
    check("analyze requires auth", client.post("/api/analyze").status_code in (401, 422))
    check("weak password rejected",
          client.post("/api/auth/signup", data={"email": "a@b.co", "password": "short"}).status_code == 400)
    check("bad email rejected",
          client.post("/api/auth/signup", data={"email": "nope", "password": "correcthorse1"}).status_code == 400)

    alice = TestClient(app)
    r = alice.post("/api/auth/signup", data={"email": "alice@example.com", "password": "correcthorse1"})
    check("signup alice", r.status_code == 200, r.text[:200])
    check("session cookie set", "bgc_session" in r.cookies or alice.cookies.get("bgc_session") is not None)

    dup = TestClient(app).post("/api/auth/signup", data={"email": "alice@example.com", "password": "correcthorse1"})
    check("duplicate email rejected", dup.status_code == 409)

    status = alice.get("/api/status").json()
    check("status shows free plan", status["usage"]["plan"] == "free", str(status["usage"]))
    check("free quota is 2", status["usage"]["quota"] == 2, str(status["usage"]))

    bad_login = TestClient(app).post("/api/auth/login", data={"email": "alice@example.com", "password": "wrongwrong1"})
    check("wrong password rejected", bad_login.status_code == 401)

    relogin = TestClient(app)
    ok_login = relogin.post("/api/auth/login", data={"email": "alice@example.com", "password": "correcthorse1"})
    check("correct password accepted", ok_login.status_code == 200, ok_login.text[:200])
    check("logged in session works", relogin.get("/api/analyses").status_code == 200)
    relogin.post("/api/auth/logout")
    check("password roundtrip unit", _password_roundtrip())

    ids = []
    for i in range(2):
        with SAMPLE.open("rb") as fh:
            resp = alice.post("/api/analyze", files={"file": (f"tender{i}.pdf", fh, "application/pdf")},
                              data={"profile": "", "profile_name": ""})
        check(f"upload {i + 1} accepted", resp.status_code == 200, resp.text[:200])
        ids.append(resp.json()["id"])

    over = alice.post("/api/analyze",
                      files={"file": ("t.pdf", SAMPLE.read_bytes(), "application/pdf")})
    check("quota enforced", over.status_code == 402, f"status={over.status_code}")

    row = wait(alice, ids[0])
    check("analysis completes", row["status"] == "done", str(row.get("error"))[:200])
    result = row["result"]
    check("deadlines extracted", len(result["deadlines"]) >= 3, str(len(result["deadlines"])))
    check("requirements extracted", result["stats"]["requirements"] >= 10)
    check("quotes verified", all(x.get("verified") for x in result["requirements"]))
    check("every requirement has a page", all(isinstance(x["page"], int) and x["page"] > 0 for x in result["requirements"]))

    check("markdown report", alice.get(f"/api/analyses/{ids[0]}/report.md").status_code == 200)
    check("html report", alice.get(f"/api/analyses/{ids[0]}/report.html").status_code == 200)
    check("source pdf", alice.get(f"/api/analyses/{ids[0]}/source.pdf").status_code == 200)

    bob = TestClient(app)
    bob.post("/api/auth/signup", data={"email": "bob@example.com", "password": "correcthorse1"})
    check("tenant isolation - detail", bob.get(f"/api/analyses/{ids[0]}").status_code == 404)
    check("tenant isolation - report", bob.get(f"/api/analyses/{ids[0]}/report.html").status_code == 404)
    check("tenant isolation - list", bob.get("/api/analyses").json() == [])

    check("upgrade not configured yet", alice.post("/api/billing/checkout", data={"plan_key": "monthly"}).status_code == 503)

    up = alice.post("/api/billing/dev-upgrade")
    check("dev upgrade", up.status_code == 200, up.text[:200])
    usage = alice.get("/api/status").json()["usage"]
    check("plan is pro", usage["plan"] == "pro", str(usage))
    check("quota lifted", usage["quota"] == 50 and usage["unlimited"] is True)

    third = alice.post("/api/analyze", files={"file": ("t3.pdf", SAMPLE.read_bytes(), "application/pdf")})
    check("upload allowed after upgrade", third.status_code == 200)

    bad_sig = alice.post("/api/billing/webhook", json={"meta": {"event_name": "order_created"}},
                         headers={"X-Signature": "deadbeef"})
    check("webhook rejects bad signature", bad_sig.status_code == 401)

    os.environ["LS_SIGNING_SECRET"] = "testsecret"
    from app import billing
    billing.LS_SIGNING_SECRET = "testsecret"
    import hashlib
    import hmac
    import json as _json
    payload = {"meta": {"event_name": "subscription_cancelled",
                        "custom_data": {"user_id": status["user"]["id"]}},
               "data": {"id": "1", "type": "subscriptions",
                        "attributes": {"customer_id": 7, "subscription_id": 9,
                                       "ends_at": "2099-01-01T00:00:00Z"}}}
    raw = _json.dumps(payload).encode()
    sig = hmac.new(b"testsecret", raw, hashlib.sha256).hexdigest()
    hook = alice.post("/api/billing/webhook", content=raw,
                      headers={"X-Signature": sig, "Content-Type": "application/json"})
    check("webhook accepts valid signature", hook.status_code == 200, hook.text[:200])
    check("cancel keeps access until ends_at",
          alice.get("/api/status").json()["usage"]["plan"] == "pro")

    check("profile saved", alice.post("/api/profiles", data={"name": "acme", "body": "turnover 6M"}).status_code == 200)
    check("profile listed", len(alice.get("/api/profiles").json()) == 1)
    check("profile isolated", bob.get("/api/profiles/acme").status_code == 404)

    check("logout", alice.post("/api/auth/logout").status_code == 200)
    check("session dead after logout", alice.get("/api/analyses").status_code == 401)

print()
print(f"{len(passed)} passed, {len(failed)} failed")
if failed:
    print("FAILURES:")
    for name in failed:
        print("  -", name)
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if failed else 0)