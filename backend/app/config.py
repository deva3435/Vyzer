from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str
    access_token_expire_minutes: int
    cors_origins: tuple[str, ...]
    port: int
    frontend_url: str | None
    rate_limit_per_minute: int

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def load_settings() -> Settings:
    environment = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).strip().lower()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./vyzer.db").strip()
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://"): ]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"): ]
    jwt_secret = os.getenv("JWT_SECRET_KEY", "").strip()
    algorithm = os.getenv("JWT_ALGORITHM", "HS256").strip()
    origins = _csv(os.getenv("CORS_ORIGINS", os.getenv("ALLOWED_ORIGINS", "http://localhost:8501")))

    if environment == "production":
        if len(jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters in production")
        if not origins or "*" in origins:
            raise RuntimeError("CORS_ORIGINS must contain explicit origins in production")
        if "localhost" in database_url or "127.0.0.1" in database_url:
            raise RuntimeError("Production DATABASE_URL must not point to localhost")
        if algorithm != "HS256":
            raise RuntimeError("Only HS256 is supported by this deployment configuration")

    return Settings(
        environment=environment,
        database_url=database_url,
        jwt_secret_key=jwt_secret or secrets.token_urlsafe(48),
        jwt_algorithm=algorithm or "HS256",
        access_token_expire_minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")),
        cors_origins=origins,
        port=int(os.getenv("PORT", "8000")),
        frontend_url=os.getenv("FRONTEND_URL") or None,
        rate_limit_per_minute=int(os.getenv("AUTH_RATE_LIMIT_PER_MINUTE", "10")),
    )


settings = load_settings()
