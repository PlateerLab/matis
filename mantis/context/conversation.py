"""대화 컨텍스트 관리."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """단일 메시지."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


class ConversationContext:
    """대화 메시지 목록 관리.

    멀티턴 대화의 메시지를 쌓고, LLM에 넘길 형태로 변환한다.
    """

    def __init__(self, system_prompt: str = ""):
        self._messages: list[Message] = []
        self._system_prompt = system_prompt

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str):
        self._system_prompt = value

    def add_user(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str | None = None, tool_calls: list[dict] | None = None):
        self._messages.append(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self._messages.append(
            Message(role="tool", content=content, tool_call_id=tool_call_id, name=name)
        )

    def to_messages(self) -> list[dict]:
        """LLM API에 넘길 messages 배열 생성."""
        msgs = []
        if self._system_prompt:
            msgs.append({"role": "system", "content": self._system_prompt})
        msgs.extend(m.to_dict() for m in self._messages)
        return msgs

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
