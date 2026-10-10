import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DEFAULT_DB = (Path(__file__).resolve().parent.parent / "data" / "bidgetcheck.db")
DATABASE_URL = os.getenv("BC_DATABASE_URL", f"sqlite:///{DEFAULT_DB.as_posix()}")
SESSION_TTL_DAYS = int(os.getenv("BC_SESSION_TTL_DAYS", "30"))
FREE_MONTHLY_QUOTA = int(os.getenv("BC_FREE_QUOTA", "3"))
FREE_MAX_PAGES = int(os.getenv("BC_FREE_MAX_PAGES", "80"))
PRO_MONTHLY_QUOTA = int(os.getenv("BC_PRO_QUOTA", "150"))

Base = declarative_base()


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_recycle": 280, "pool_size": 5, "max_overflow": 10}


engine = create_engine(DATABASE_URL, future=True, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"
    id = Column(String(32), primary_key=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    plan = Column(String(16), default="free", nullable=False)
    plan_expires_at = Column(DateTime(timezone=True))
    ls_customer_id = Column(String(64))
    ls_subscription_id = Column(String(64))
    ls_order_id = Column(String(64))
    onboarding_done = Column(Boolean, default=False, nullable=False)

    sessions = relationship("SessionRow", back_populates="user", cascade="all, delete-orphan")
    analyses = relationship("Analysis", back_populates="user", cascade="all, delete-orphan")
    profiles = relationship("Profile", back_populates="user", cascade="all, delete-orphan")


class SessionRow(Base):
    __tablename__ = "sessions"
    token = Column(String(64), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    user = relationship("User", back_populates="sessions")


class Analysis(Base):
    __tablename__ = "analyses"
    id = Column(String(32), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename = Column(String(512), nullable=False)
    pages = Column(Integer, default=0, nullable=False)
    status = Column(String(16), default="queued", nullable=False)
    provider = Column(String(24), default="none")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    finished_at = Column(DateTime(timezone=True))
    result = Column(JSON)
    error = Column(Text)

    user = relationship("User", back_populates="analyses")


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(String(32), primary_key=True)
    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name = Column(String(160), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="profiles")


class Waitlist(Base):
    __tablename__ = "waitlist"
    id = Column(String(32), primary_key=True)
    email = Column(String(320), nullable=False)
    company = Column(String(200))
    tender = Column(String(400))
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class EventLog(Base):
    __tablename__ = "event_log"
    id = Column(String(32), primary_key=True)
    user_id = Column(String(32))
    analysis_id = Column(String(32))
    event = Column(String(48), nullable=False)
    detail = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


Index("ix_analyses_user_created", Analysis.user_id, Analysis.created_at)


def new_id() -> str:
    return secrets.token_hex(8)


def session_scope():
    return SessionLocal()


def init_db() -> None:
    Base.metadata.create_all(engine)


def log_event(event: str, user_id: str | None = None, analysis_id: str | None = None, detail: str = "") -> None:
    with SessionLocal() as s:
        s.add(EventLog(id=new_id(), user_id=user_id, analysis_id=analysis_id,
                       event=event, detail=detail[:4000]))
        s.commit()


def quota_for(plan: str) -> int:
    return PRO_MONTHLY_QUOTA if plan == "pro" else FREE_MONTHLY_QUOTA


def max_pages_for(plan: str) -> int:
    return 100_000 if plan == "pro" else FREE_MAX_PAGES


def month_start() -> datetime:
    now = utcnow()
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def usage_this_month(user_id: str | None) -> int:
    with SessionLocal() as s:
        rows = (
            s.query(Analysis)
            .filter(Analysis.user_id == user_id, Analysis.created_at >= month_start())
            .count()
        )
    return rows


def is_pro(user) -> bool:
    if user is None:
        return False
    if user.plan != "pro":
        return False
    if user.plan_expires_at is None:
        return True
    expires = user.plan_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > utcnow()


def grant_plan(user_id: str, plan: str, months: int = 1,
               customer_id: str | None = None, subscription_id: str | None = None,
               order_id: str | None = None, ends_at: datetime | None = None) -> None:
    with SessionLocal() as s:
        user = s.get(User, user_id)
        if user is None:
            log_event("grant_failed_unknown_user", detail=user_id)
            return
        user.plan = plan
        if plan != "pro":
            user.plan_expires_at = None
        elif ends_at is not None:
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=UTC)
            user.plan_expires_at = ends_at
        else:
            base = user.plan_expires_at if is_pro(user) and user.plan_expires_at else utcnow()
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            user.plan_expires_at = base + timedelta(days=31 * months)
        if customer_id:
            user.ls_customer_id = customer_id
        if subscription_id:
            user.ls_subscription_id = subscription_id
        if order_id:
            user.ls_order_id = order_id
        s.commit()
    log_event("plan_granted", user_id=user_id, detail=f"{plan} ends={user.plan_expires_at}")


def revoke_plan(user_id: str) -> None:
    with SessionLocal() as s:
        user = s.get(User, user_id)
        if user is None:
            return
        user.plan = "free"
        user.plan_expires_at = None
        s.commit()
    log_event("plan_revoked", user_id=user_id)


def create_analysis(user_id: str | None, aid: str, filename: str, pages: int, provider: str) -> None:
    with SessionLocal() as s:
        s.add(Analysis(id=aid, user_id=user_id, filename=filename, pages=pages,
                       status="queued", provider=provider))
        s.commit()


def update_analysis(aid: str, **fields) -> None:
    if not fields:
        return
    with SessionLocal() as s:
        row = s.get(Analysis, aid)
        if row is None:
            return
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        s.commit()


def get_analysis(aid: str, user_id: str | None = None, require_owner: bool = False) -> dict | None:
    with SessionLocal() as s:
        row = s.get(Analysis, aid)
        if row is None:
            return None
        if require_owner and row.user_id != user_id:
            return None
        return {
            "id": row.id, "user_id": row.user_id, "filename": row.filename,
            "pages": row.pages, "status": row.status, "provider": row.provider,
            "created_at": row.created_at, "finished_at": row.finished_at,
            "result": row.result, "error": row.error,
        }


def list_analyses(user_id: str | None = None, limit: int = 50) -> list[dict]:
    with SessionLocal() as s:
        q = s.query(Analysis).order_by(Analysis.created_at.desc())
        if user_id is not None:
            q = q.filter(Analysis.user_id == user_id)
        rows = q.limit(limit).all()
    return [{"id": r.id, "filename": r.filename, "pages": r.pages, "status": r.status,
             "provider": r.provider, "created_at": r.created_at, "finished_at": r.finished_at,
             "error": r.error} for r in rows]


def save_profile(user_id: str | None, name: str, body: str) -> str:
    now = utcnow()
    with SessionLocal() as s:
        existing = (s.query(Profile)
                    .filter(Profile.user_id == user_id, Profile.name == name)
                    .first())
        if existing:
            existing.body = body
            existing.updated_at = now
            pid = existing.id
        else:
            pid = new_id()
            s.add(Profile(id=pid, user_id=user_id, name=name, body=body,
                          created_at=now, updated_at=now))
        s.commit()
    return pid


def get_profile(user_id: str | None, name: str) -> dict | None:
    with SessionLocal() as s:
        row = (s.query(Profile)
               .filter(Profile.user_id == user_id, Profile.name == name)
               .first())
        return {"id": row.id, "name": row.name, "body": row.body,
                "created_at": row.created_at, "updated_at": row.updated_at} if row else None


def list_profiles(user_id: str | None = None) -> list[dict]:
    with SessionLocal() as s:
        q = s.query(Profile).order_by(Profile.name)
        if user_id is not None:
            q = q.filter(Profile.user_id == user_id)
        rows = q.all()
    return [{"id": r.id, "name": r.name, "created_at": r.created_at, "updated_at": r.updated_at}
            for r in rows]


def delete_profile(user_id: str | None, name: str) -> bool:
    with SessionLocal() as s:
        row = (s.query(Profile)
               .filter(Profile.user_id == user_id, Profile.name == name)
               .first())
        if row is None:
            return False
        s.delete(row)
        s.commit()
    return True


def add_waitlist(email: str, company: str = "", tender: str = "", note: str = "") -> str:
    wid = new_id()
    with SessionLocal() as s:
        s.add(Waitlist(id=wid, email=email.strip().lower(), company=company.strip(),
                       tender=tender.strip(), note=note.strip()))
        s.commit()
    return wid


def list_waitlist(limit: int = 200) -> list[dict]:
    with SessionLocal() as s:
        rows = s.query(Waitlist).order_by(Waitlist.created_at.desc()).limit(limit).all()
    return [{"email": r.email, "company": r.company, "tender": r.tender,
             "note": r.note, "created_at": r.created_at} for r in rows]