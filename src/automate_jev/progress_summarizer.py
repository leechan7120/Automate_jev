from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable, Protocol

from .models import ContractError
from .slack import SlackMessage


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    progress: tuple[str, ...]
    discussions: tuple[str, ...]
    decisions: tuple[str, ...]
    project_overview: str = ""


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
    deadline_seconds: float = 30.0
    client_factory: Callable[[str], Any] = field(default=_default_client_factory, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ContractError("Gemini API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ContractError("Gemini model must be configured with 1 to 128 characters")
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
                response = await client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
            raw = str(getattr(response, "text", ""))
            if not raw:
                raise ValueError("Gemini returned an empty summary")
            return _parse_summary(raw)
        except (TimeoutError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Gemini progress summary was invalid") from error
        finally:
            close = getattr(getattr(client, "aio", None), "aclose", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=3)
                except Exception:
                    pass
def _prompt(messages: tuple[SlackMessage, ...], existing_root: str) -> str:
    evidence = "\n".join(
        f"[{message.channel or 'unknown'} / {message.author or 'unknown'}] {_redact(message.text)}"
        for message in messages
    )
    return (
        "Summarize a project's Slack activity into strict JSON. Do not invent facts, names, "
        "dates, decisions, or progress. Each bullet must be supported by the messages. "
        "Use concise Korean when the messages are Korean, otherwise use the dominant language. "
        "Return exactly these keys: progress (array of strings), discussions (array of strings), "
        "decisions (array of strings), project_overview (string). project_overview should be a "
        "short updated project description based only on this activity and the existing root context. "
        "Keep each array to at most 8 bullets and each bullet under 300 characters.\n\n"
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
    return ProgressSummary(bullets("progress"), bullets("discussions"), bullets("decisions"), overview)


def _redact(value: str) -> str:
    return re.sub(
        r"(?i)\b(password|passcode|otp|token|api[_-]?key|secret|authorization|cookie)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        value[:2_000],
    )
