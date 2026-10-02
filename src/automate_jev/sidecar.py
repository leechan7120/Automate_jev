from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .models import Action, ContractError
from .workflow import WorkflowDefinition


MAX_SIDECAR_BYTES = 256 * 1024
MAX_SIDECAR_EVENTS = 10_000


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class SidecarEvidence:
    version: int
    event_count: int
    accepted_event_count: int
    action_ids: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event_count": self.event_count,
            "accepted_event_count": self.accepted_event_count,
            "action_ids": list(self.action_ids),
        }


def validate_sidecar(
    content: bytes,
    *,
    actions: Iterable[Action],
    workflow: WorkflowDefinition | None = None,
) -> SidecarEvidence:
    if not 1 <= len(content) <= MAX_SIDECAR_BYTES:
        raise ContractError(f"sidecar must contain 1 to {MAX_SIDECAR_BYTES} bytes")
    try:
        document = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ContractError("sidecar is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ContractError("sidecar root must be an object")
    version = document.get("version")
    if isinstance(version, bool) or version not in {1, 2}:
        raise ContractError("sidecar version is unsupported")
    events = document.get("events")
    if not isinstance(events, list) or len(events) > MAX_SIDECAR_EVENTS:
        raise ContractError(f"sidecar events must be an array of at most {MAX_SIDECAR_EVENTS} items")

    text_actions = {
        str(action.arguments.get("text")): action.id
        for action in actions
        if action.verb == "fill" and isinstance(action.arguments.get("text"), str)
    }
    matched: list[str] = []
    accepted_count = 0
    for event in events:
        if not isinstance(event, dict):
            raise ContractError("sidecar event must be an object")
        kind = event.get("actionKind")
        if kind != "TextEntry" or event.get("isQuarantined") is True:
            continue
        accepted_count += 1
        text = event.get("actionText")
        if not isinstance(text, str) or text not in text_actions:
            raise ContractError("sidecar TextEntry is not bound to a registered action")
        action_id = text_actions[text]
        if action_id not in matched:
            matched.append(action_id)

    if accepted_count == 0:
        raise ContractError("sidecar contains no accepted TextEntry evidence")
    if workflow is not None:
        expected = {step.action_id for step in workflow.steps}
        if not expected <= set(matched):
            raise ContractError("sidecar evidence does not match the extracted workflow")
    return SidecarEvidence(
        version=int(version),
        event_count=len(events),
        accepted_event_count=accepted_count,
        action_ids=tuple(matched),
    )
