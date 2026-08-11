from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .database import Base, engine, get_db
from .dependencies import get_current_user
from .models import Conversation, Message, User, utcnow
from .schemas import (
    AuthResponse,
    ConversationCreate,
    ConversationList,
    ConversationOut,
    ConversationRename,
    ConversationResponse,
    Credentials,
    MessageCreate,
    MessageOut,
    UserOut,
)
from .security import create_access_token, hash_password, verify_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vyzer.backend")


class InMemoryRateLimiter:
    """Per-process abuse protection. Use a shared limiter for multi-instance deployments."""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self.events[key]
        while bucket and now - bucket[0] >= self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


rate_limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Migrations are intentionally not run automatically in production.
    if settings.environment != "production":
        Base.metadata.create_all(bind=engine)
    logger.info("Vyzer backend started environment=%s", settings.environment)
    yield
    logger.info("Vyzer backend stopped")


app = FastAPI(
    title="Vyzer Backend",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_and_logging(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error request_id=%s path=%s", request_id, request.url.path)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/health/db")
def db_health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception:
        logger.exception("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable")


def _rate_limit(request: Request, bucket: str) -> None:
    host = request.client.host if request.client else "unknown"
    if not rate_limiter.allowed(f"{bucket}:{host}"):
        raise HTTPException(status_code=429, detail="Too many requests")


def _conversation_out(conversation: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                metadata=m.metadata_json or {},
                created_at=m.created_at,
            )
            for m in conversation.messages
        ],
        document=conversation.document,
        updated_at=conversation.updated_at,
    )


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=201)
def signup(payload: Credentials, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request, "signup")
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Account already exists")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Account already exists")
    db.refresh(user)
    return AuthResponse(token=create_access_token(user.id), user=UserOut.model_validate(user))


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: Credentials, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request, "login")
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return AuthResponse(token=create_access_token(user.id), user=UserOut.model_validate(user))


@app.post("/api/auth/reset-password")
def reset_password_legacy(payload: Credentials, request: Request, db: Session = Depends(get_db)):
    """Development-only compatibility endpoint; deliberately unavailable in production."""
    _rate_limit(request, "reset")
    if settings.is_production:
        raise HTTPException(
            status_code=410,
            detail="Password reset requires an email-verified recovery flow and is not enabled on this deployment",
        )
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    user.password_hash = hash_password(payload.password)
    db.commit()
    return AuthResponse(token=create_access_token(user.id), user=UserOut.model_validate(user))


@app.get("/api/conversations", response_model=ConversationList)
def list_conversations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.updated_at.desc())
    ).all()
    return ConversationList(conversations=[_conversation_out(row) for row in rows])


@app.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
        .options(selectinload(Conversation.messages))
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationResponse(conversation=_conversation_out(conversation))


@app.post("/api/conversations", response_model=ConversationResponse, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = Conversation(user_id=current_user.id, title=payload.title, updated_at=utcnow())
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationResponse(conversation=_conversation_out(conversation))


@app.put("/api/conversations/{conversation_id}", response_model=ConversationResponse)
def rename_conversation(
    conversation_id: str,
    payload: ConversationRename,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.title is not None:
        conversation.title = payload.title
    if payload.document is not None:
        conversation.document = payload.document
    conversation.updated_at = utcnow()
    db.commit()
    db.refresh(conversation)
    return ConversationResponse(conversation=_conversation_out(conversation))


@app.post("/api/conversations/{conversation_id}/messages", response_model=MessageOut, status_code=201)
def add_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message = Message(
        conversation_id=conversation.id,
        role=payload.role,
        content=payload.content,
        metadata_json=payload.metadata,
    )
    conversation.updated_at = utcnow()
    db.add(message)
    db.commit()
    db.refresh(message)
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        metadata=message.metadata_json or {},
        created_at=message.created_at,
    )


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conversation)
    db.commit()
    return None
