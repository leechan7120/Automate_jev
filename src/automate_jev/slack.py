from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .mcp import MCPToolClient
from .models import ContractError


@dataclass(frozen=True, slots=True)
class SlackMessage:
    message_id: str
    channel: str
    author: str
    text: str
    timestamp: str = ""

    def line(self) -> str:
        source = " / ".join(value for value in (self.channel, self.author) if value)
        return f"- {source}: {self.text}" if source else f"- {self.text}"


@dataclass(slots=True)
class SlackMCP:
    """Searches Slack through a configured MCP server."""

    client: MCPToolClient
    server: str = "slack"
    search_tool: str = "slack-search-messages"
    timezone_name: str = "UTC"

    async def search_messages(self, day: date, query: str = "") -> tuple[SlackMessage, ...]:
        if not isinstance(day, date):
            raise ContractError("Slack progress date is invalid")
        search_query = query.strip() or (
            f"after:{(day - timedelta(days=1)).isoformat()} "
            f"before:{(day + timedelta(days=2)).isoformat()}"
        )
        if len(search_query) > 512:
            raise ContractError("Slack search query is too long")
        result = await self.client.call_tool(
            server=self.server,
            tool=self.search_tool,
            arguments={"query": search_query},
        )
        messages: list[SlackMessage] = []
        for index, item in enumerate(_result_items(result)):
            text = _text(item)
            if not text:
                continue
            messages.append(
                SlackMessage(
                    message_id=str(item.get("id", item.get("ts", f"message-{index}"))),
                    channel=str(item.get("channel", item.get("channel_name", ""))),
                    author=str(item.get("author", item.get("user", item.get("username", "")))),
                    text=text[:2_000],
                    timestamp=str(item.get("ts", item.get("timestamp", ""))),
                )
            )
        if query.strip():
            return tuple(messages)
        return tuple(message for message in messages if _belongs_to_day(message, day, self.timezone_name))


def _result_items(result: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw: Any = result.get("structuredContent", result.get("messages", result.get("results", result.get("content", ()))))
    if isinstance(raw, Mapping):
        raw = raw.get("messages", raw.get("results", raw.get("content", ())))
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _text(item: Mapping[str, Any]) -> str:
    raw = item.get("text", item.get("message", item.get("content", "")))
    if isinstance(raw, Mapping):
        raw = raw.get("text", raw.get("content", ""))
    return str(raw).strip()


def _belongs_to_day(message: SlackMessage, day: date, timezone_name: str) -> bool:
    if not message.timestamp:
        return True
    try:
        local_zone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        local_zone = timezone.utc
    try:
        timestamp = float(message.timestamp)
        message_day = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(local_zone).date()
    except ValueError:
        try:
            message_day = datetime.fromisoformat(message.timestamp.replace("Z", "+00:00")).astimezone(local_zone).date()
        except ValueError:
            return True
    return message_day == day
