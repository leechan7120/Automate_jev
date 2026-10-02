from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable, Protocol

from .models import ContractError
from .slack import SlackMessage


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    name: str
    overview: str
    progress: tuple[str, ...]
    discussions: tuple[str, ...]
    decisions: tuple[str, ...]
    next_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    progress: tuple[str, ...]
    discussions: tuple[str, ...]
    decisions: tuple[str, ...]
    project_overview: str = ""
    projects: tuple[ProjectSummary, ...] = ()
    implementation_approach: str = ""


class ProgressSummarizer(Protocol):
    async def summarize(
        self,
        *,
        messages: tuple[SlackMessage, ...],
        existing_root: str = "",
    ) -> ProgressSummary: ...


def _default_client_factory(api_key: str) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=35_000))


@dataclass(slots=True)
class GeminiProgressSummarizer:
    api_key: str = field(repr=False)
    model: str
    fallback_model: str = "gemini-3.5-flash-lite"
    deadline_seconds: float = 30.0
    client_factory: Callable[[str], Any] = field(default=_default_client_factory, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ContractError("Gemini API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ContractError("Gemini model must be configured with 1 to 128 characters")
        if not self.fallback_model.strip() or len(self.fallback_model) > 128:
            raise ContractError("Gemini fallback model must be configured with 1 to 128 characters")
        if not 5 <= self.deadline_seconds <= 120:
            raise ContractError("Gemini summary deadline must be between 5 and 120 seconds")

    async def summarize(
        self,
        *,
        messages: tuple[SlackMessage, ...],
        existing_root: str = "",
    ) -> ProgressSummary:
        if not messages:
            return ProgressSummary((), (), (), "")
        prompt = _prompt(messages, existing_root)
        client = self.client_factory(self.api_key)
        try:
            async with asyncio.timeout(self.deadline_seconds):
                try:
                    response = await _generate_content(client, self.model, prompt)
                except Exception as error:
                    if self.fallback_model == self.model or not _is_transient_error(error):
                        raise
                    response = await _generate_content(client, self.fallback_model, prompt)
            raw = str(getattr(response, "text", ""))
            if not raw:
                raise ValueError("Gemini returned an empty summary")
            return _parse_summary(raw)
        except Exception as error:
            raise RuntimeError("Gemini progress summary was invalid") from error
        finally:
            close = getattr(getattr(client, "aio", None), "aclose", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=3)
                except Exception:
                    pass


async def _generate_content(client: Any, model: str, prompt: str) -> Any:
    return await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )


def _is_transient_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True
    message = str(error)
    return any(marker in message for marker in ("429", "500", "502", "503", "504"))
def _prompt(messages: tuple[SlackMessage, ...], existing_root: str) -> str:
    evidence = "\n".join(
        f"[{message.timestamp or 'unknown'} / {message.channel or 'unknown'} / {message.author or 'unknown'}] {_redact(message.text)}"
        for message in messages
    )
    return (
    "Analyze Slack activity into strict JSON. Do not invent facts, names, dates, decisions, "
    "or progress. Each bullet must be supported by the messages. "
        "Use concise Korean when the messages are Korean, otherwise use the dominant language. "
    "Return exactly these keys: progress, discussions, decisions, project_overview, implementation_approach, projects. "
    "Only include a project when the messages contain enough evidence of a concrete workstream, "
    "product, feature, client, or initiative. Exclude greetings, isolated personal notes, and "
    "unrelated chatter. For each project return name, overview, progress, discussions, "
    "decisions, and next_actions. Keep at most 8 projects and at most 8 bullets per array. "
    "Keep each bullet under 300 characters.\n\n"
        f"Existing project root context:\n{existing_root[:3_000]}\n\nSlack evidence:\n{evidence[:12_000]}"
    )


def _parse_summary(raw: str) -> ProgressSummary:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("summary must be an object")

    def bullets(name: str) -> tuple[str, ...]:
        items = value.get(name, [])
        if not isinstance(items, list):
            raise ValueError(f"{name} must be an array")
        return tuple(str(item).strip()[:300] for item in items[:8] if str(item).strip())

    overview = str(value.get("project_overview", "")).strip()[:1_000]
    implementation = str(value.get("implementation_approach", "")).strip()[:1_500]
    projects: list[ProjectSummary] = []
    raw_projects = value.get("projects", [])
    if not isinstance(raw_projects, list):
        raise ValueError("projects must be an array")
    for raw_project in raw_projects[:8]:
        if not isinstance(raw_project, dict):
            continue
        name = str(raw_project.get("name", "")).strip()[:120]
        if not name:
            continue
        projects.append(ProjectSummary(
            name=name,
            overview=str(raw_project.get("overview", "")).strip()[:1_000],
            progress=tuple(_project_bullets(raw_project, "progress")),
            discussions=tuple(_project_bullets(raw_project, "discussions")),
            decisions=tuple(_project_bullets(raw_project, "decisions")),
            next_actions=tuple(_project_bullets(raw_project, "next_actions")),
        ))
    return ProgressSummary(
        bullets("progress"),
        bullets("discussions"),
        bullets("decisions"),
        overview,
        tuple(projects),
        implementation,
    )


def _project_bullets(value: dict[str, Any], name: str) -> list[str]:
    items = value.get(name, [])
    if not isinstance(items, list):
        return []
    return [str(item).strip()[:300] for item in items[:8] if str(item).strip()]


def _redact(value: str) -> str:
    return re.sub(
        r"(?i)\b(password|passcode|otp|token|api[_-]?key|secret|authorization|cookie)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        value[:2_000],
    )
