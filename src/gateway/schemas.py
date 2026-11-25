"""Request and response shapes, compatible with the OpenAI chat completions API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float = Field(default=1.0, ge=0, le=2)
    stream: bool = False


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class ChatResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rate_limit_per_minute: int = Field(default=60, ge=1)
    daily_token_budget: int | None = Field(default=None, ge=1)


class TeamUpdate(BaseModel):
    """Only the fields the caller sends are changed. An explicit null means 'unlimited'."""

    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    daily_token_budget: int | None = Field(default=None, ge=1)


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rate_limit_per_minute: int
    daily_token_budget: int | None


class ApiKeyCreated(BaseModel):
    id: int
    team_id: int
    prefix: str
    api_key: str  # the only time the full key is ever shown


class UsageOut(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
