import hashlib
import hmac
import os
import time
from datetime import datetime

import httpx

from . import db

API_BASE = "https://api.lemonsqueezy.com/v1"
LS_API_KEY = os.getenv("LS_API_KEY", "")
LS_STORE_ID = os.getenv("LS_STORE_ID", "")
LS_SIGNING_SECRET = os.getenv("LS_SIGNING_SECRET", "")
LS_VARIANT_MONTHLY = os.getenv("LS_VARIANT_MONTHLY", "")
LS_VARIANT_ONEOFF = os.getenv("LS_VARIANT_ONEOFF", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
BILLING_DEV = os.getenv("BC_BILLING_DEV", "0") == "1"
APP_ENV = os.getenv("BC_ENV", "development")

MONTHS = 1
PRICE_MONTHLY = "$29"
PRICE_ONEOFF = "$49"
TRIAL_TEXT = "7-day free trial"

if APP_ENV == "production":
    BILLING_DEV = False


class BillingNotConfigured(Exception):
    pass


def configured() -> bool:
    return bool(LS_API_KEY and LS_STORE_ID)


def dev_enabled() -> bool:
    return BILLING_DEV


def variant_for(plan_key: str) -> str:
    if plan_key == "monthly":
        return LS_VARIANT_MONTHLY
    if plan_key == "oneoff":
        return LS_VARIANT_ONEOFF
    return ""


def create_checkout(user_id: str, email: str, plan_key: str) -> str:
    if not configured():
        raise BillingNotConfigured("Lemon Squeezy is not configured")
    variant = variant_for(plan_key)
    if not variant:
        raise BillingNotConfigured(f"Variant for '{plan_key}' is not configured")

    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "custom": {"user_id": user_id},
                    "email": email,
                },
                "product_id": int(LS_STORE_ID),
                "variant_id": int(variant),
                "expires_at": (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + 900)) + "Z"),
                "checkout_options": {"embed": False, "media": True, "logo": True, "desc": True},
            },
        }
    }
    headers = {
        "X-Api-Key": LS_API_KEY,
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    resp = httpx.post(f"{API_BASE}/checkouts", json=payload, headers=headers, timeout=20.0)
    if resp.status_code >= 400:
        db.log_event("checkout_failed", user_id=user_id,
                     detail=f"status={resp.status_code} body={resp.text[:300]}")
        raise BillingNotConfigured(f"Checkout failed with status {resp.status_code}")
    return resp.json()["data"]["attributes"]["url"]


def verify_signature(raw_body: bytes, signature: str) -> bool:
    if not LS_SIGNING_SECRET or not signature:
        return False
    digest = hmac.new(LS_SIGNING_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def _meta(payload: dict) -> tuple[str | None, str | None]:
    meta = payload.get("meta") or {}
    custom = meta.get("custom_data") or {}
    return custom.get("user_id"), meta.get("store_id")


def _is_live(payload: dict) -> bool:
    return bool(payload.get("meta", {}).get("event_name") and payload.get("data", {}).get("id"))


def _parse_ends_at(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def handle_event(payload: dict) -> dict:
    name = (payload.get("meta") or {}).get("event_name", "")
    user_id, store_id = _meta(payload)

    if LS_STORE_ID and store_id and str(store_id) != str(LS_STORE_ID):
        return {"status": "ignored", "reason": "store mismatch", "event": name}

    if not user_id:
        db.log_event("webhook_no_user", detail=f"event={name}")
        return {"status": "ignored", "reason": "no user_id in meta", "event": name}

    attrs = (payload.get("data") or {}).get("attributes", {}) or {}
    customer_id = attrs.get("customer_id")
    subscription_id = attrs.get("subscription_id")
    order_id = (payload.get("data") or {}).get("id")
    ends_at = _parse_ends_at(attrs.get("ends_at"))

    if name in ("order_created", "subscription_created", "subscription_updated",
                "subscription_resumed", "license_key_created"):
        db.grant_plan(user_id, "pro", months=MONTHS, customer_id=customer_id,
                      subscription_id=subscription_id, order_id=order_id, ends_at=ends_at)
        return {"status": "granted", "plan": "pro", "event": name}

    if name in ("subscription_cancelled", "subscription_paused"):
        db.grant_plan(user_id, "pro", months=0, customer_id=customer_id,
                      subscription_id=subscription_id, ends_at=ends_at)
        return {"status": "kept_until_expiry", "event": name}

    if name in ("subscription_expired", "subscription_unpaid"):
        db.revoke_plan(user_id)
        return {"status": "revoked", "event": name}

    if name == "refund_created":
        db.revoke_plan(user_id)
        return {"status": "revoked", "event": name}

    return {"status": "ignored", "event": name}


def store_page_url() -> str:
    if not configured():
        return ""
    return "https://store.lemonsqueezy.com"