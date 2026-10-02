from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Action, ContractError
from .workflow import WorkflowDefinition, parse_workflow


MAX_VIDEO_BYTES = 50 * 1024 * 1024
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
WORKFLOW_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class VideoExtractionError(RuntimeError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _draft_schema(actions: tuple[Action, ...]) -> dict[str, Any]:
    action_ids = [action.id for action in actions]
    risks = sorted({action.risk.value for action in actions})
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["steps", "completion"],
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": min(12, len(actions)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "domain", "action_id", "success", "risk"],
                    "properties": {
                        "id": {"type": "string"},
                        "domain": {
                            "type": "string",
                            "enum": ["browser", "desktop", "filesystem", "system"],
                        },
                        "action_id": {"type": "string", "enum": action_ids},
                        "success": {"type": "object"},
                        "risk": {"type": "string", "enum": risks},
                    },
                },
            },
            "completion": {
                "type": "object",
                "required": ["all"],
                "properties": {
                    "all": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "object"},
                    }
                },
            },
        },
    }


def _validate_video(path: Path, mime_type: str) -> None:
    if mime_type not in ALLOWED_VIDEO_TYPES:
        raise ContractError("unsupported video MIME type")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ContractError("video file is unavailable") from error
    if size < 1 or size > MAX_VIDEO_BYTES:
        raise ContractError(f"video must contain 1 to {MAX_VIDEO_BYTES} bytes")


def _finalize_draft(
    draft: Any,
    *,
    workflow_id: str,
    allowed_root: str,
    actions: tuple[Action, ...],
) -> WorkflowDefinition:
    if not isinstance(draft, dict):
        raise ContractError("extracted workflow draft must be an object")
    document = {
        "schema_version": "1.0",
        "workflow_id": workflow_id,
        "allowed_root": allowed_root,
        "steps": draft.get("steps"),
        "completion": draft.get("completion"),
    }
    workflow = parse_workflow(document)
    workflow.bind_actions(actions)
    return workflow


@dataclass(slots=True)
class FixtureVideoExtractor:
    async def extract(
        self,
        video_path: Path,
        *,
        mime_type: str,
        workflow_id: str,
        allowed_root: str,
        actions: Iterable[Action],
    ) -> WorkflowDefinition:
        _validate_video(video_path, mime_type)
        materialized = tuple(actions)
        if len(materialized) != 1:
            raise ContractError("fixture extraction requires exactly one registered action")
        action = materialized[0]
        effect = dict(action.expected_effects[0])
        success = {str(effect["key"]): effect.get("value")}
        return _finalize_draft(
            {
                "steps": [
                    {
                        "id": "extracted-step-1",
                        "domain": action.domain,
                        "action_id": action.id,
                        "success": success,
                        "risk": action.risk.value,
                    }
                ],
                "completion": {"all": [success]},
            },
            workflow_id=workflow_id,
            allowed_root=allowed_root,
            actions=materialized,
        )


def _default_client_factory(api_key: str) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=15_000),
    )


@dataclass(slots=True)
class GeminiVideoExtractor:
    api_key: str = field(repr=False)
    model: str
    deadline_seconds: float = 60.0
    client_factory: Callable[[str], Any] = field(default=_default_client_factory, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ContractError("Gemini API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ContractError("Gemini model must contain 1 to 128 characters")
        if not 10 <= self.deadline_seconds <= 120:
            raise ContractError("Gemini deadline must be between 10 and 120 seconds")

    async def extract(
        self,
        video_path: Path,
        *,
        mime_type: str,
        workflow_id: str,
        allowed_root: str,
        actions: Iterable[Action],
    ) -> WorkflowDefinition:
        _validate_video(video_path, mime_type)
        if not WORKFLOW_ID.fullmatch(workflow_id):
            raise ContractError("workflow_id is invalid")
        materialized = tuple(actions)
        if not 1 <= len(materialized) <= 12:
            raise ContractError("video extraction requires 1 to 12 registered actions")
        client = self.client_factory(self.api_key)
        try:
            async with asyncio.timeout(self.deadline_seconds):
                draft = await self._extract_async(
                    client,
                    video_path,
                    mime_type,
                    materialized,
                )
        except TimeoutError:
            raise VideoExtractionError("Gemini extraction exceeded its deadline") from None
        finally:
            close = getattr(getattr(client, "aio", None), "aclose", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=3)
                except Exception:
                    pass
        return _finalize_draft(
            draft,
            workflow_id=workflow_id,
            allowed_root=allowed_root,
            actions=materialized,
        )

    async def _extract_async(
        self,
        client: Any,
        video_path: Path,
        mime_type: str,
        actions: tuple[Action, ...],
    ) -> Any:
        uploaded = None
        try:
            uploaded = await client.aio.files.upload(file=str(video_path))
            while True:
                state = getattr(getattr(uploaded, "state", None), "name", None)
                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    raise VideoExtractionError("Gemini could not process the video")
                await asyncio.sleep(2)
                uploaded = await client.aio.files.get(name=uploaded.name)
            catalog = [
                {
                    "id": action.id,
                    "domain": action.domain,
                    "risk": action.risk.value,
                    "description": action.description,
                }
                for action in actions
            ]
            interaction = await client.aio.interactions.create(
                model=self.model,
                input=[
                    {"type": "video", "uri": uploaded.uri, "mime_type": mime_type},
                    {
                        "type": "text",
                        "text": (
                            "Extract a minimal workflow draft from this demonstration. Use only "
                            f"these registered actions: {json.dumps(catalog, ensure_ascii=False)}. "
                            "Do not invent targets, arguments, credentials, or filesystem paths."
                        ),
                    },
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": _draft_schema(actions),
                },
            )
            output = str(interaction.output_text)
            if len(output.encode("utf-8")) > 256 * 1024:
                raise VideoExtractionError("Gemini workflow response exceeded 256 KiB")
            return json.loads(output, object_pairs_hook=_no_duplicate_keys)
        except (VideoExtractionError, ContractError):
            raise
        except Exception as error:
            raise VideoExtractionError("Gemini returned an invalid workflow draft") from error
        finally:
            if uploaded is not None:
                try:
                    await asyncio.wait_for(
                        client.aio.files.delete(name=uploaded.name),
                        timeout=3,
                    )
                except Exception:
                    pass
