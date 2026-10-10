import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 64
PBKDF2_ROUNDS = 260_000
MAX_PASSWORD_LEN = 256
MAX_ATTEMPTS = 8
LOCKOUT_BASE_SECONDS = 60

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email))) and len(email) <= 320


def hash_password(password: str) -> str:
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError("password too long")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(password) > MAX_PASSWORD_LEN:
        return f"Password must be at most {MAX_PASSWORD_LEN} characters."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
    if classes < 2:
        return "Use at least two of: lowercase, uppercase, digits, symbols."
    return None


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


_attempts: dict[str, list[float]] = {}
_lock = threading.Lock()


def register_failure(key: str) -> int:
    now = time.time()
    with _lock:
        hits = [t for t in _attempts.get(key, []) if now - t < 3600]
        hits.append(now)
        _attempts[key] = hits
        return len(hits)


def clear_failures(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)


def retry_after(key: str) -> int:
    now = time.time()
    with _lock:
        hits = _attempts.get(key, [])
    if len(hits) < MAX_ATTEMPTS:
        return 0
    oldest = min(hits)
    wait = LOCKOUT_BASE_SECONDS * (2 ** (len(hits) - MAX_ATTEMPTS))
    remaining = int(wait - (now - oldest))
    return max(remaining, 0)


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def safe_filename(name: str) -> str:
    name = (name or "document.pdf").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", name).strip() or "document.pdf"
    return name[:180]


def is_pdf(data: bytes) -> bool:
    return len(data) > 5 and data[:5] == b"%PDF-"