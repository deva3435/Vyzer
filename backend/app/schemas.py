from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


PASSWORD_MIN = 10
PASSWORD_MAX = 128
CONTENT_MAX = 200_000
METADATA_MAX_KEYS = 30


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    created_at: datetime


class AuthResponse(BaseModel):
    token: str
    user: UserOut


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("title cannot be empty")
        return value


class ConversationRename(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    document: dict[str, Any] | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("title cannot be empty")
        return value


class MessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=CONTENT_MAX)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content cannot be empty")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > METADATA_MAX_KEYS:
            raise ValueError("metadata contains too many keys")
        for key in value:
            if len(key) > 100:
                raise ValueError("metadata key is too long")
        return value


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    title: str
    messages: list[MessageOut]
    document: dict[str, Any] | None = None
    updated_at: datetime


class ConversationList(BaseModel):
    conversations: list[ConversationOut]


class ConversationResponse(BaseModel):
    conversation: ConversationOut
