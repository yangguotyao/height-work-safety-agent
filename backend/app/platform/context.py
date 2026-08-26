from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .repository import PlatformRepository


@dataclass(frozen=True)
class ContextPacket:
    source: str
    content: str
    timestamp: str
    relevance: float
    metadata: dict[str, Any]


class ContextBuilder:
    """Builds a bounded, source-labelled context instead of forwarding full history."""

    def __init__(
        self,
        repository: PlatformRepository,
        *,
        message_limit: int = 12,
        char_budget: int = 12000,
    ):
        self.repository = repository
        self.message_limit = message_limit
        self.char_budget = char_budget

    def build(
        self,
        *,
        conversation_id: str,
        identity: dict[str, Any],
        query: str,
    ) -> list[dict[str, Any]]:
        packets: list[ContextPacket] = []
        for message in self.repository.messages(conversation_id, self.message_limit):
            packets.append(
                ContextPacket(
                    source="short_term_message",
                    content=f"{message['role']}: {message['content']}",
                    timestamp=str(message["created_at"]),
                    relevance=0.8 if message["role"] == "user" else 0.6,
                    metadata={"message_id": message["id"], "agent": message["agent_name"]},
                )
            )
        for memory in self.repository.search_memories(identity, query):
            packets.append(
                ContextPacket(
                    source=f"long_term_{memory['memory_type']}",
                    content=str(memory["content"]),
                    timestamp=str(memory["updated_at"]),
                    relevance=float(memory["importance"]),
                    metadata={"memory_id": memory["id"], "subject_key": memory["subject_key"]},
                )
            )
        packets.sort(key=lambda item: (-item.relevance, item.timestamp))
        selected: list[dict[str, Any]] = []
        used = 0
        for packet in packets:
            remaining = self.char_budget - used
            if remaining <= 0:
                break
            content = packet.content[:remaining]
            selected.append(asdict(packet) | {"content": content})
            used += len(content)
        return selected


class MemoryManager:
    def __init__(self, repository: PlatformRepository):
        self.repository = repository

    def remember_explicit_profile(
        self,
        identity: dict[str, Any],
        *,
        conversation_id: str,
        message: str,
    ) -> None:
        normalized = message.strip()
        explicit_markers = ("请记住", "记住：", "以后都", "我的班组", "我的工种")
        if not any(marker in normalized for marker in explicit_markers):
            return
        self.repository.save_memory(
            identity,
            memory_type="profile",
            subject_key="explicit-user-preference",
            content=normalized,
            importance=0.9,
            source_type="conversation",
            source_id=conversation_id,
        )

    def update_conversation_summary(
        self, conversation_id: str, user_message: str, agent_name: str, answer: str
    ) -> None:
        summary = f"用户：{user_message[:500]}\n{agent_name}：{answer[:1000]}"
        self.repository.update_summary(conversation_id, summary)
