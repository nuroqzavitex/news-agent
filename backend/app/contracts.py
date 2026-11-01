"""Contract chung giữa các agent: Agent Card, Message Envelope, Principal (spec §6.1)."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MessageType = Literal["request", "response", "final", "error"]
ErrorCode = Literal["bad_request", "unauthorized", "timeout", "unavailable", "internal"]
AgentStatus = Literal["online", "degraded", "offline"]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class AgentCard(BaseModel):
    agent_id: str
    group_id: str
    domain: str
    capabilities: list[str]
    endpoint: str
    status: AgentStatus = "online"
    version: str = "1.0.0"


class Envelope(BaseModel):
    """Message Envelope. Trường `from` là từ khóa Python nên dùng `from_` + alias."""

    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(default_factory=lambda: new_id("m"))
    conversation_id: str = Field(default_factory=lambda: new_id("conv"))
    trace_id: str = Field(default_factory=lambda: new_id("tr"))
    from_: str = Field(alias="from")
    to: str
    type: MessageType = "request"
    capability: str | None = None
    reply_to: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    token: str | None = None
    timestamp: float = Field(default_factory=time.time)

    def wire(self) -> dict[str, Any]:
        """Dạng JSON gửi qua mạng (dùng key `from`)."""
        return self.model_dump(mode="json", by_alias=True)

    def reply(self, *, sender: str, payload: dict[str, Any], type: MessageType = "response") -> Envelope:
        """Reply giữ nguyên conversation_id + trace_id, reply_to = message_id gốc."""
        return Envelope(
            conversation_id=self.conversation_id,
            trace_id=self.trace_id,
            from_=sender,
            to=self.from_,
            type=type,
            capability=self.capability,
            reply_to=self.message_id,
            payload=payload,
        )

    def error_reply(self, *, sender: str, code: ErrorCode, message: str) -> Envelope:
        return self.reply(sender=sender, type="error", payload={"error": code, "message": message})


class Principal(BaseModel):
    """Danh tính đã xác thực từ token (Team 3 hoặc local)."""

    agent_id: str
    expires_at: float
