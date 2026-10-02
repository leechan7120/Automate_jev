from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from pathlib import Path
import re
from typing import Iterable

from .activity import ObservedActivity
from .models import ContractError, canonical_json


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    SITE = "site"
    ROUTINE = "routine"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    kind: MemoryKind
    scope: str
    text: str
    source: tuple[str, ...]
    confidence: float
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.scope.strip() or len(self.scope) > 128:
            raise ContractError("memory scope is invalid")
        if not self.text.strip() or len(self.text) > 4_000:
            raise ContractError("memory text is invalid")
        if not 0 <= self.confidence <= 1:
            raise ContractError("memory confidence must be between 0 and 1")
        if self.created_at.tzinfo is None:
            raise ContractError("memory timestamp must include a timezone")

    @property
    def memory_id(self) -> str:
        payload = {"kind": self.kind.value, "scope": self.scope, "text": self.text}
        return "memory:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:24]

    def payload(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "scope": self.scope,
            "text": self.text,
            "source": list(self.source),
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True)
class LocalMemoryStore:
    """Inspectable local-first memory, organized like Aside's memory files."""

    root: Path

    def __post_init__(self) -> None:
        for directory in ("episodic", "semantic", "sites", "routines"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)

    def upsert(self, record: MemoryRecord) -> None:
        directory = {
            MemoryKind.EPISODIC: "episodic",
            MemoryKind.SEMANTIC: "semantic",
            MemoryKind.SITE: "sites",
            MemoryKind.ROUTINE: "routines",
        }[record.kind]
        safe_scope = re.sub(r"[^A-Za-z0-9._-]+", "_", record.scope)[:96]
        path = self.root / directory / f"{safe_scope}.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {record.scope}\n"
        if record.memory_id not in existing:
            existing += (
                f"\n## {record.memory_id}\n"
                f"- confidence: {record.confidence:.2f}\n"
                f"- created_at: {record.created_at.isoformat()}\n"
                f"- source: {', '.join(record.source) or 'local-observation'}\n\n"
                f"{record.text}\n"
            )
            path.write_text(existing, encoding="utf-8", newline="\n")

    def records(self) -> tuple[MemoryRecord, ...]:
        records: list[MemoryRecord] = []
        for path in self.root.rglob("*.jsonl"):
            raise ContractError(f"unsupported memory file: {path.name}")
        return tuple(records)

    def search(self, query: str, *, limit: int = 8) -> tuple[str, ...]:
        if not query.strip() or not 1 <= limit <= 32:
            raise ContractError("memory search query or limit is invalid")
        terms = set(re.findall(r"[\w.-]+", query.lower()))
        matches: list[tuple[int, str]] = []
        for path in self.root.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            score = sum(text.lower().count(term) for term in terms)
            if score:
                matches.append((score, text))
        return tuple(text for _, text in sorted(matches, key=lambda item: item[0], reverse=True)[:limit])


@dataclass(slots=True)
class ActivityMemoryExtractor:
    """Creates inspectable memories from observed activity without raw browser content."""

    store: LocalMemoryStore

    def consolidate(self, activities: Iterable[ObservedActivity]) -> tuple[MemoryRecord, ...]:
        ordered = sorted(activities, key=lambda activity: activity.observed_at)
        if not ordered:
            return ()
        records: list[MemoryRecord] = []
        for activity in ordered:
            source = (activity.observed_at.isoformat(),)
            record = MemoryRecord(
                kind=MemoryKind.SITE,
                scope=activity.surface,
                text=f"The user worked in {activity.surface} on '{activity.topic}' around {activity.observed_at.strftime('%H:%M')}.",
                source=source,
                confidence=0.5,
                created_at=datetime.now(timezone.utc),
            )
            self.store.upsert(record)
            records.append(record)
        return tuple(records)