from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .bridge import BridgeError, JsonLinesBridgeClient
from .models import ActionEnvelope, ContractError, Observation
from .registry import RegistrySnapshot


class BridgeRuntimeAdapter:
    def __init__(self, bridge: JsonLinesBridgeClient) -> None:
        self.bridge = bridge

    async def register(self, snapshot: RegistrySnapshot) -> None:
        result = await self.bridge.call(
            "register_actions",
            {
                "session_id": snapshot.session_id,
                "registry_version": snapshot.version,
                "actions": [
                    {"id": action.id, "action_hash": action.action_hash}
                    for action in snapshot.actions
                ],
            },
        )
        if result.get("registered") != len(snapshot.actions):
            raise ContractError("Windows Host did not register every action")

    async def observe(self) -> Observation:
        result = await self.bridge.call("observe")
        try:
            facts = result["facts"]
            if not isinstance(facts, dict):
                raise TypeError("facts")
            return Observation(
                schema_version=str(result["schema_version"]),
                state_revision=str(result["state_revision"]),
                captured_at=str(result.get("captured_at") or datetime.now(timezone.utc).isoformat()),
                foreground_surface=str(result["foreground_surface"]),
                facts=facts,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError("Windows Host observation is malformed") from error

    async def execute_if_current(self, envelope: ActionEnvelope) -> None:
        payload: dict[str, Any] = {
            "session_id": envelope.session_id,
            "registry_version": envelope.registry_version,
            "action_id": envelope.action.id,
            "action_hash": envelope.action_hash,
            "expires_at": envelope.expires_at,
            "idempotency_key": envelope.idempotency_key,
            "expected_state_revision": envelope.expected_state_revision,
            "target": dict(envelope.action.target),
            "arguments": dict(envelope.action.arguments),
            "preconditions": [dict(item) for item in envelope.action.preconditions],
            "expected_effects": [dict(item) for item in envelope.action.expected_effects],
        }
        try:
            result = await self.bridge.call("execute_registered", payload)
        except BridgeError as error:
            if error.code in {"STALE_ACTION", "TARGET_MISMATCH"}:
                raise ContractError(f"target or revision mismatch: {error.code}") from None
            raise ContractError(f"Windows Host rejected execution: {error.code}") from None
        if result.get("execution_state") != "confirmed":
            raise ContractError("Windows Host execution was not confirmed")
