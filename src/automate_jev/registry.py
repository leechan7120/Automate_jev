from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Iterable

from .models import Action, ActionEnvelope, ContractError, Observation


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    session_id: str
    version: int
    actions: tuple[Action, ...]


class ActionRegistry:
    """Session-bound action registry. Models only receive IDs and descriptions."""

    def __init__(self, session_id: str) -> None:
        if not session_id:
            raise ContractError("session_id is required")
        self._session_id = session_id
        self._version = 0
        self._actions: dict[str, Action] = {}
        self._lock = RLock()

    def replace(self, actions: Iterable[Action]) -> RegistrySnapshot:
        materialized = tuple(actions)
        if not 1 <= len(materialized) <= 12:
            raise ContractError("registry requires 1 to 12 actions")
        by_id = {action.id: action for action in materialized}
        if len(by_id) != len(materialized):
            raise ContractError("action ids must be unique")
        with self._lock:
            self._actions = by_id
            self._version += 1
            return self.snapshot()

    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return RegistrySnapshot(
                session_id=self._session_id,
                version=self._version,
                actions=tuple(self._actions.values()),
            )

    def candidate_descriptions(self) -> dict[str, str]:
        with self._lock:
            candidates = {action.id: action.description for action in self._actions.values()}
        candidates["abstain"] = "No safe executable action is available."
        return candidates

    def envelope(
        self,
        action_id: str,
        observation: Observation,
        idempotency_key: str,
        *,
        ttl_seconds: int = 60,
    ) -> ActionEnvelope:
        if not 1 <= ttl_seconds <= 300:
            raise ContractError("ttl_seconds must be between 1 and 300")
        with self._lock:
            try:
                action = self._actions[action_id]
            except KeyError as error:
                raise ContractError("action is not registered") from error
            return ActionEnvelope(
                session_id=self._session_id,
                registry_version=self._version,
                action=action,
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
                idempotency_key=idempotency_key,
                expected_state_revision=observation.state_revision,
            )

    def validate(self, envelope: ActionEnvelope) -> None:
        with self._lock:
            if envelope.session_id != self._session_id:
                raise ContractError("session mismatch")
            if envelope.registry_version != self._version:
                raise ContractError("registry version mismatch")
            registered = self._actions.get(envelope.action.id)
            if registered is None or registered.action_hash != envelope.action_hash:
                raise ContractError("action hash mismatch")
            if envelope.is_expired():
                raise ContractError("action expired")

