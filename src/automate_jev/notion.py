from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Mapping

from .mcp import MCPToolClient
from .memory import LocalMemoryStore, MemoryKind, MemoryRecord
from .models import ContractError


@dataclass(frozen=True, slots=True)
class NotionPage:
    page_id: str
    title: str
    text: str
    url: str = ""
    last_edited_at: datetime | None = None


@dataclass(slots=True)
class NotionMCP:
    """Notion memory adapter over any MCP client, including the official server."""

    client: MCPToolClient
    server: str = "notion"
    access_tool: str = "notion-get-tool-access"
    search_tool: str = "notion-search"
    fetch_tool: str = "notion-fetch"
    create_tool: str = "notion-create-pages"
    update_tool: str = "notion-update-page"

    async def search(self, query: str) -> tuple[NotionPage, ...]:
        if not query.strip() or len(query) > 512:
            raise ContractError("Notion search query is invalid")
        await self.client.call_tool(
            server=self.server,
            tool=self.access_tool,
            arguments={},
        )
        result = await self.client.call_tool(
            server=self.server,
            tool=self.search_tool,
            arguments={"query": query},
        )
        return tuple(_page_from_mapping(item) for item in _result_items(result))

    async def fetch(self, page_id: str) -> NotionPage:
        if not page_id.strip():
            raise ContractError("Notion page id is required")
        result = await self.client.call_tool(
            server=self.server,
            tool=self.fetch_tool,
            arguments={"id": page_id},
        )
        items = _result_items(result)
        if not items:
            raise ContractError("Notion fetch returned no page")
        return _page_from_mapping(items[0])

    async def sync_to_memory(self, query: str, store: LocalMemoryStore) -> tuple[MemoryRecord, ...]:
        pages = await self.search(query)
        records: list[MemoryRecord] = []
        for page in pages:
            if not page.text:
                page = await self.fetch(page.page_id)
            edited = page.last_edited_at or datetime.now(timezone.utc)
            record = MemoryRecord(
                kind=MemoryKind.SEMANTIC,
                scope=f"notion-{page.page_id}",
                text=f"{page.title}\n{page.text}"[:4_000],
                source=("notion", page.page_id, page.url),
                confidence=0.9,
                created_at=edited,
            )
            store.upsert(record)
            records.append(record)
        return tuple(records)

    async def publish_routine(self, *, title: str, content: str, parent_id: str) -> Mapping[str, Any]:
        if not title.strip() or not content.strip() or not parent_id.strip():
            raise ContractError("Notion routine publication requires title, content, and parent_id")
        return await self.client.call_tool(
            server=self.server,
            tool=self.create_tool,
            arguments={
                "allow_async": False,
                "parent": {"page_id": parent_id},
                "pages": [{
                    "properties": {"title": title[:200]},
                    "content": content[:4_000],
                }],
            },
        )

    async def update_page(self, *, page_id: str, title: str, content: str) -> Mapping[str, Any]:
        if not page_id.strip() or not title.strip() or not content.strip():
            raise ContractError("Notion page update requires page_id, title, and content")
        return await self.client.call_tool(
            server=self.server,
            tool=self.update_tool,
            arguments={
                "allow_async": False,
                "page_id": page_id,
                "command": "replace_content",
                "new_str": content[:4_000],
            },
        )


def _result_items(result: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw: Any = result.get("structuredContent", result.get("results", result.get("content", ())))
    if isinstance(raw, Mapping):
        if isinstance(raw.get("results"), (list, tuple)):
            raw = raw["results"]
        elif isinstance(raw.get("content"), (list, tuple)):
            raw = raw["content"]
        elif isinstance(raw.get("page"), Mapping):
            raw = [raw["page"]]
        elif raw.get("id"):
            raw = [raw]
        else:
            raw = ()
    if isinstance(raw, (list, tuple)) and raw and all(isinstance(item, Mapping) and item.get("type") == "text" for item in raw):
        decoded: list[Mapping[str, Any]] = []
        for item in raw:
            try:
                value = json.loads(str(item.get("text", "")))
            except (TypeError, ValueError):
                continue
            if isinstance(value, Mapping):
                nested = value.get("results", value)
                if isinstance(nested, list):
                    decoded.extend(item for item in nested if isinstance(item, Mapping))
        raw = decoded
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _page_from_mapping(value: Mapping[str, Any]) -> NotionPage:
    page_id = str(value.get("id", "")).strip()
    if not page_id:
        raise ContractError("Notion search result has no page id")
    properties = value.get("properties", {})
    title = str(value.get("title", "")) or _property_text(properties, "title") or "Untitled Notion page"
    text = str(value.get("text", value.get("content", "")))
    edited_raw = value.get("last_edited_time")
    edited = datetime.fromisoformat(str(edited_raw).replace("Z", "+00:00")) if edited_raw else None
    return NotionPage(
        page_id=page_id,
        title=title[:512],
        text=text[:3_500],
        url=str(value.get("url", ""))[:1_000],
        last_edited_at=edited,
    )


def _property_text(properties: Any, name: str) -> str:
    if not isinstance(properties, Mapping):
        return ""
    value = properties.get(name, properties.get(name.capitalize(), {}))
    if not isinstance(value, Mapping):
        return ""
    raw = value.get("title", value.get("rich_text", ()))
    if not isinstance(raw, (list, tuple)):
        return ""
    return "".join(
        str(item.get("plain_text", item.get("text", {}).get("content", "")))
        for item in raw
        if isinstance(item, Mapping)
    ).strip()