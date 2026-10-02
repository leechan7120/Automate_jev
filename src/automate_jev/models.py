from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import math
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when an object violates the local execution contract."""


class Risk(StrEnum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ContractError("value must contain finite JSON data") from error


def digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ContractError(f"{name} must contain 1 to 128 characters")
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-:" for character in value):
        raise ContractError(f"{name} contains unsupported characters")


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    domain: str
    verb: str
    description: str
    target: Mapping[str, Any] = field(default_factory=dict)
    arguments: Mapping[str, Any] = field(default_factory=dict)
    preconditions: tuple[Mapping[str, Any], ...] = ()
    expected_effects: tuple[Mapping[str, Any], ...] = ()
    risk: Risk = Risk.SAFE
    source: str = "workflow"

    def __post_init__(self) -> None:
        _validate_identifier(self.id, "action id")
        _validate_identifier(self.domain, "domain")
        _validate_identifier(self.verb, "verb")
        if not self.description.strip() or len(self.description) > 2_000:
            raise ContractError("description must contain 1 to 2000 characters")
        if self.source not in {"workflow", "recovery", "system"}:
            raise ContractError("source must be workflow, recovery, or system")
        encoded = canonical_json(self.payload())
        if len(encoded.encode("utf-8")) > 32_768:
            raise ContractError("action exceeds 32 KiB")

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "verb": self.verb,
            "description": self.description,
            "target": dict(self.target),
            "arguments": dict(self.arguments),
            "preconditions": [dict(item) for item in self.preconditions],
            "expected_effects": [dict(item) for item in self.expected_effects],
            "risk": self.risk.value,
            "source": self.source,
        }

    @property
    def action_hash(self) -> str:
        return digest_json(self.payload())


@dataclass(frozen=True, slots=True)
class Observation:
    schema_version: str
    state_revision: str
    captured_at: str
    foreground_surface: str
    facts: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported observation schema version")
        if not self.state_revision.startswith("sha256:"):
            raise ContractError("state_revision must be a sha256 fingerprint")
        canonical_json(dict(self.facts))

    @classmethod
    def capture(cls, foreground_surface: str, facts: Mapping[str, Any]) -> Observation:
        stable = {"foreground_surface": foreground_surface, "facts": dict(facts)}
        return cls(
            schema_version="1.0",
            state_revision=digest_json(stable),
            captured_at=datetime.now(timezone.utc).isoformat(),
            foreground_surface=foreground_surface,
            facts=dict(facts),
        )


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    session_id: str
    registry_version: int
    action: Action
    expires_at: str
    idempotency_key: str
    expected_state_revision: str

    def __post_init__(self) -> None:
        _validate_identifier(self.session_id, "session id")
        _validate_identifier(self.idempotency_key, "idempotency key")
        if isinstance(self.registry_version, bool) or self.registry_version < 1:
            raise ContractError("registry_version must be positive")
        if not self.expected_state_revision.startswith("sha256:"):
            raise ContractError("expected_state_revision must be a sha256 fingerprint")
        self.expiry()

    @property
    def action_hash(self) -> str:
        return self.action.action_hash

    @property
    def arguments_hash(self) -> str:
        return digest_json(dict(self.action.arguments))

    def expiry(self) -> datetime:
        try:
            parsed = datetime.fromisoformat(self.expires_at)
        except ValueError as error:
            raise ContractError("expires_at must be ISO-8601") from error
        if parsed.tzinfo is None:
            raise ContractError("expires_at must include a timezone")
        return parsed

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expiry()


@dataclass(frozen=True, slots=True)
class Decision:
    action_id: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.action_id:
            raise ContractError("decision action_id is required")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ContractError("confidence must be numeric")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ContractError("confidence must be between 0 and 1")

