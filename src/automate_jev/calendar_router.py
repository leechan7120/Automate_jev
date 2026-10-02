from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import os
import shlex
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .env import load_gemini_settings
from .mcp import MCPStdioClient, MCPToolClient
from .models import ContractError
from .progress_summarizer import _redact
from .slack import SlackMCP, SlackMessage


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    summary: str
    start_date: str
    start_time: str
    end_date: str
    end_time: str
    timezone: str
    all_day: bool
    description: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalendarDecision:
    events: tuple[CalendarEvent, ...]
    rejection_reasons: tuple[str, ...] = ()


class CalendarDecisionProvider:
    async def decide(
        self,
        *,
        messages: tuple[SlackMessage, ...],
        reference_day: date,
        timezone_name: str,
    ) -> CalendarDecision:
        raise NotImplementedError


def _default_client_factory(api_key: str) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=35_000))


@dataclass(slots=True)
class GeminiCalendarDecisionProvider(CalendarDecisionProvider):
    api_key: str = field(repr=False)
    model: str
    fallback_model: str = "gemini-3.5-flash-lite"
    deadline_seconds: float = 30.0
    client_factory: Callable[[str], Any] = field(default=_default_client_factory, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.model.strip():
            raise ContractError("Gemini calendar decision requires an API key and model")
        if not 5 <= self.deadline_seconds <= 120:
            raise ContractError("Gemini calendar decision deadline must be between 5 and 120 seconds")

    async def decide(
        self,
        *,
        messages: tuple[SlackMessage, ...],
        reference_day: date,
        timezone_name: str,
    ) -> CalendarDecision:
        if not messages:
            return CalendarDecision(())
        evidence = "\n".join(
            f"[{message.message_id}] {message.timestamp or 'unknown'} / {message.channel or 'unknown'} / "
            f"{message.author or 'unknown'}: {_redact(message.text)}"
            for message in messages
        )
        prompt = f"""
You are a strict calendar action judge. Analyze the Slack conversation and return JSON only.
Reference date: {reference_day.isoformat()}
Timezone: {timezone_name}

Create an event only when ALL conditions are true:
1. The conversation explicitly confirms a final date, not a suggestion, question, possibility,
   tentative plan, deadline guess, or unresolved proposal.
2. The conversation clearly decides what must happen, such as a meeting, appointment, review,
   delivery, presentation, or other concrete task.
3. The event has enough information for a calendar entry. A confirmed date without a confirmed
   time is allowed only as an all-day event. Do not invent missing times.

Treat phrases equivalent to 'maybe', 'how about', 'we should', 'can we', '予定', '検討',
'아마', '어떨까요', '논의 필요', or '미정' as NOT confirmed unless a later message clearly
resolves them. A date mentioned as background or a deadline that is not agreed as an event is
not a calendar event. Use only facts supported by the messages. Keep the evidence_ids.

Return exactly:
{{"events":[{{"confirmed":true,"summary":"...","start_date":"YYYY-MM-DD",
"start_time":"HH:MM or empty","end_date":"YYYY-MM-DD or empty",
"end_time":"HH:MM or empty","all_day":true,"description":"...",
"evidence_ids":["message-id"]}}]}}

Return an empty events array for anything uncertain. Never return an event with confirmed=false.
Slack evidence:
{evidence[:16_000]}
""".strip()
        client = self.client_factory(self.api_key)
        try:
            async with asyncio.timeout(self.deadline_seconds):
                response = await self._generate(client, self.model, prompt)
            raw = str(getattr(response, "text", ""))
            if not raw:
                raise ValueError("Gemini returned an empty calendar decision")
            return _parse_decision(raw, messages, timezone_name)
        except Exception as error:
            raise RuntimeError("Gemini calendar decision was invalid") from error
        finally:
            close = getattr(getattr(client, "aio", None), "aclose", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=3)
                except Exception:
                    pass

    async def _generate(self, client: Any, model: str, prompt: str) -> Any:
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
        except Exception as error:
            if self.fallback_model == self.model or not any(
                marker in str(error) for marker in ("429", "500", "502", "503", "504")
            ):
                raise
            return await client.aio.models.generate_content(
                model=self.fallback_model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )


def _parse_decision(
    raw: str,
    messages: tuple[SlackMessage, ...],
    timezone_name: str,
) -> CalendarDecision:
    value = json.loads(raw)
    if not isinstance(value, Mapping) or not isinstance(value.get("events", []), list):
        raise ValueError("calendar decision must contain an events array")
    message_ids = {message.message_id for message in messages}
    events: list[CalendarEvent] = []
    rejection_reasons: list[str] = []
    for item in value["events"][:8]:
        if not isinstance(item, Mapping) or item.get("confirmed") is not True:
            rejection_reasons.append("not_confirmed")
            continue
        summary = str(item.get("summary", "")).strip()[:200]
        start_date = str(item.get("start_date", "")).strip()
        start_time = str(item.get("start_time", "")).strip()
        if not summary or not _valid_date(start_date) or not _valid_time(start_time):
            rejection_reasons.append("invalid_summary_or_start")
            continue
        evidence_ids = tuple(
            str(identifier) for identifier in item.get("evidence_ids", [])
            if str(identifier) in message_ids
        )
        if not evidence_ids:
            rejection_reasons.append("missing_evidence")
            continue
        end_date = str(item.get("end_date", "")).strip()
        end_time = str(item.get("end_time", "")).strip()
        all_day = bool(item.get("all_day", not start_time))
        if all_day:
            start_time = ""
            end_time = ""
        elif not _valid_time(end_time) or not end_time:
            rejection_reasons.append("missing_end_time")
            continue
        events.append(CalendarEvent(
            summary=summary,
            start_date=start_date,
            start_time=start_time,
            end_date=end_date if _valid_date(end_date) else start_date,
            end_time=end_time,
            timezone=timezone_name,
            all_day=all_day,
            description=str(item.get("description", "")).strip()[:1_000],
            evidence_ids=evidence_ids,
        ))
    return CalendarDecision(tuple(events), tuple(rejection_reasons))


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _valid_time(value: str) -> bool:
    if not value:
        return True
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


@dataclass(slots=True)
class SlackCalendarRouter:
    slack: SlackMCP
    calendar: MCPToolClient
    decision_provider: CalendarDecisionProvider
    memory_root: Path
    calendar_server: str = "calendar"
    create_tool: str = "calendar-create-event"
    timezone_name: str = "UTC"

    async def route(self, day: date, *, query: str = "") -> Mapping[str, Any]:
        messages = await self.slack.search_messages(day, query)
        decision = await self.decision_provider.decide(
            messages=messages,
            reference_day=day,
            timezone_name=self.timezone_name,
        )
        created: list[Mapping[str, Any]] = []
        skipped: list[str] = []
        for event in decision.events:
            key = _event_key(event)
            marker = self.memory_root / "calendar-events" / f"{key}.json"
            if marker.exists():
                skipped.append(event.summary)
                continue
            result = await self.calendar.call_tool(
                server=self.calendar_server,
                tool=self.create_tool,
                arguments={
                    "summary": event.summary,
                    "start_date": event.start_date,
                    "start_time": event.start_time,
                    "end_date": event.end_date,
                    "end_time": event.end_time,
                    "timezone": event.timezone,
                    "all_day": event.all_day,
                    "description": event.description,
                    "source_message_ids": list(event.evidence_ids),
                },
            )
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"summary": event.summary, "result": dict(result)}), encoding="utf-8")
            created.append({"summary": event.summary, "result": dict(result)})
        return {
            "date": day.isoformat(),
            "message_count": len(messages),
            "decision_count": len(decision.events),
            "llm_rejection_reasons": list(decision.rejection_reasons),
            "created": created,
            "skipped_duplicates": skipped,
            "llm_decision": True,
        }


def _event_key(event: CalendarEvent) -> str:
    payload = {
        "summary": event.summary,
        "start_date": event.start_date,
        "start_time": event.start_time,
        "end_date": event.end_date,
        "end_time": event.end_time,
        "evidence_ids": event.evidence_ids,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _command(name: str) -> tuple[str, ...]:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return tuple(shlex.split(value, posix=os.name != "nt"))


async def sync(day: date) -> None:
    calendar_client = MCPStdioClient(_command("AUTOMATE_JEV_CALENDAR_MCP_COMMAND"))
    slack_client = MCPStdioClient(_command("AUTOMATE_JEV_SLACK_MCP_COMMAND"))
    try:
        api_key, model = load_gemini_settings()
        router = SlackCalendarRouter(
            slack=SlackMCP(
                slack_client,
                server=os.environ.get("AUTOMATE_JEV_SLACK_SERVER", "slack"),
                search_tool=os.environ.get("AUTOMATE_JEV_SLACK_SEARCH_TOOL", "slack-search-messages"),
                timezone_name=os.environ.get("AUTOMATE_JEV_TIMEZONE", "UTC"),
            ),
            calendar=calendar_client,
            decision_provider=GeminiCalendarDecisionProvider(api_key=api_key, model=model),
            memory_root=Path(os.environ.get("AUTOMATE_JEV_MEMORY_ROOT", ".automate-jev/memory")),
            calendar_server=os.environ.get("AUTOMATE_JEV_CALENDAR_SERVER", "calendar"),
            create_tool=os.environ.get("AUTOMATE_JEV_CALENDAR_CREATE_TOOL", "calendar-create-event"),
            timezone_name=os.environ.get("AUTOMATE_JEV_TIMEZONE", "UTC"),
        )
        print(await router.route(day, query=os.environ.get("AUTOMATE_JEV_SLACK_QUERY", "")))
    finally:
        await slack_client.close()
        await calendar_client.close()


def main() -> None:
    timezone_name = os.environ.get("AUTOMATE_JEV_TIMEZONE", "UTC")
    parser = argparse.ArgumentParser(description="Route confirmed Slack commitments to a Calendar MCP.")
    parser.add_argument("--date", default=datetime.now(ZoneInfo(timezone_name)).date().isoformat())
    arguments = parser.parse_args()
    asyncio.run(sync(date.fromisoformat(arguments.date)))


if __name__ == "__main__":
    main()