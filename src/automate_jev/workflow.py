from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from jsonschema import Draft202012Validator

from .models import Action, ContractError


MAX_WORKFLOW_BYTES = 256 * 1024


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite number")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _read_json(path: Path, *, maximum_bytes: int) -> Any:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ContractError("workflow file is unavailable") from error
    if size < 1 or size > maximum_bytes:
        raise ContractError(f"workflow file must contain 1 to {maximum_bytes} bytes")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ContractError("workflow file is not valid UTF-8 JSON") from error


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    id: str
    domain: str
    action_id: str
    success: Mapping[str, Any]
    risk: str

    def is_satisfied(self, facts: Mapping[str, Any]) -> bool:
        return all(facts.get(key) == value for key, value in self.success.items())


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    schema_version: str
    workflow_id: str
    allowed_root: str
    steps: tuple[WorkflowStep, ...]
    completion: tuple[Mapping[str, Any], ...]

    def next_step(self, facts: Mapping[str, Any]) -> WorkflowStep | None:
        return next((step for step in self.steps if not step.is_satisfied(facts)), None)

    def is_complete(self, facts: Mapping[str, Any]) -> bool:
        return all(
            all(facts.get(key) == value for key, value in condition.items())
            for condition in self.completion
        )

    def bind_actions(self, actions: Iterable[Action]) -> tuple[Action, ...]:
        by_id = {action.id: action for action in actions}
        bound: list[Action] = []
        for step in self.steps:
            action = by_id.get(step.action_id)
            if action is None:
                raise ContractError(f"workflow action is not registered: {step.action_id}")
            if action.risk.value != step.risk:
                raise ContractError(f"workflow risk does not match registered action: {step.id}")
            bound.append(action)
        return tuple(bound)


def load_workflow(
    path: str | Path,
    *,
    schema_path: str | Path | None = None,
) -> WorkflowDefinition:
    workflow_path = Path(path)
    schema_file = (
        Path(schema_path)
        if schema_path is not None
        else Path(__file__).parents[2] / "schemas" / "workflow.schema.json"
    )
    document = _read_json(workflow_path, maximum_bytes=MAX_WORKFLOW_BYTES)
    schema = _read_json(schema_file, maximum_bytes=MAX_WORKFLOW_BYTES)
    if not isinstance(document, dict) or not isinstance(schema, dict):
        raise ContractError("workflow and schema roots must be objects")
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ContractError(f"workflow schema validation failed at {location}")

    allowed_root = str(document["allowed_root"])
    if not (
        PureWindowsPath(allowed_root).is_absolute()
        or PurePosixPath(allowed_root).is_absolute()
    ):
        raise ContractError("allowed_root must be an absolute path")
    raw_steps = document["steps"]
    step_ids = [str(item["id"]) for item in raw_steps]
    if len(step_ids) != len(set(step_ids)):
        raise ContractError("workflow step ids must be unique")

    return WorkflowDefinition(
        schema_version=str(document["schema_version"]),
        workflow_id=str(document["workflow_id"]),
        allowed_root=allowed_root,
        steps=tuple(
            WorkflowStep(
                id=str(item["id"]),
                domain=str(item["domain"]),
                action_id=str(item["action_id"]),
                success=MappingProxyType(dict(item["success"])),
                risk=str(item["risk"]),
            )
            for item in raw_steps
        ),
        completion=tuple(
            MappingProxyType(dict(condition))
            for condition in document["completion"]["all"]
        ),
    )
