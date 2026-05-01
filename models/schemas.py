from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class UserResponse(BaseModel):
    id: str
    email: str | None = None
    is_premium: bool = False


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class AuthResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: UserResponse
    needs_email_confirmation: bool = False


class ChatCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    emoji: str = Field(default="🧑", min_length=1, max_length=8)
    image_url: str | None = None
    personality: str | None = None


class ChatUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    emoji: str | None = Field(default=None, min_length=1, max_length=8)
    image_url: str | None = None
    personality: str | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str | None = None
    name: str
    emoji: str = "🧑"
    image_url: str | None = None
    personality: str | None = None
    created_at: datetime | None = None
    last_message: str | None = None
    last_message_at: datetime | None = None
    last_message_role: str | None = None


class MessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    mood_tag: str | None = Field(default=None, max_length=80)


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    chat_id: str
    role: str
    content: str
    mood_tag: str | None = None
    used_memory: bool = False
    created_at: datetime | None = None


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    next_before: datetime | None = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    user_message: MessageResponse
    ai_message: MessageResponse
    rate_limited: bool = False
    model_used: str | None = None


class MemoryResponse(BaseModel):
    id: str
    content: str
    created_at: datetime | None = None
    similarity: float | None = None


class BreathingExercise(BaseModel):
    id: str
    title: str
    inhale: int
    hold: int
    exhale: int
    hold_after_exhale: int
    description: str


class GroundingTechnique(BaseModel):
    title: str
    intro: str
    steps: list[dict[str, Any]]


class MeditationScript(BaseModel):
    id: str
    title: str
    duration_minutes: int
    script: str
