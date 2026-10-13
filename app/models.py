"""Contratos de entrada validados con Pydantic."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatCompletionRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=256, ge=1, le=4096)
    stream: Literal[False] = False  # El streaming se añadirá en una fase posterior.
