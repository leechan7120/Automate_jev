from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import os
import shlex
from pathlib import Path
from zoneinfo import ZoneInfo

from .daily_progress import DailyProgressService
from .mcp import MCPStdioClient
from .memory import LocalMemoryStore
from .notion import NotionMCP
from .progress_summarizer import GeminiProgressSummarizer
from .slack import SlackMCP
from .env import load_gemini_settings


def _command(name: str) -> tuple[str, ...]:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return tuple(shlex.split(value, posix=os.name != "nt"))


def _configured_summarizer() -> GeminiProgressSummarizer | None:
    enabled = os.environ.get("AUTOMATE_JEV_LLM_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    api_key, model = load_gemini_settings()
    return GeminiProgressSummarizer(api_key=api_key, model=model)


async def sync(day: date) -> None:
    notion_client = MCPStdioClient(_command("AUTOMATE_JEV_MCP_COMMAND"))
    slack_client = MCPStdioClient(_command("AUTOMATE_JEV_SLACK_MCP_COMMAND"))
    try:
        service = DailyProgressService(
            slack=SlackMCP(
                slack_client,
                server=os.environ.get("AUTOMATE_JEV_SLACK_SERVER", "slack"),
                search_tool=os.environ.get("AUTOMATE_JEV_SLACK_SEARCH_TOOL", "slack-search-messages"),
            ),
            notion=NotionMCP(notion_client),
            memory=LocalMemoryStore(Path(os.environ.get("AUTOMATE_JEV_MEMORY_ROOT", ".automate-jev/memory"))),
            notion_parent_id=os.environ.get("NOTION_DAILY_PROGRESS_PARENT_ID", "").strip(),
            project_root_page_id=os.environ.get("NOTION_PROJECT_ROOT_PAGE_ID", "").strip(),
            publish_enabled=os.environ.get("NOTION_PUBLISH_ENABLED", "").lower() in {"1", "true", "yes", "on"},
            summarizer=_configured_summarizer(),
        )
        progress = await service.collect_and_publish(
            day,
            query=os.environ.get("AUTOMATE_JEV_SLACK_QUERY", ""),
        )
        print(progress.payload())
    finally:
        await slack_client.close()
        await notion_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Slack activity and publish daily progress to Notion.")
    timezone_name = os.environ.get("AUTOMATE_JEV_TIMEZONE", "UTC")
    parser.add_argument("--date", default=datetime.now(ZoneInfo(timezone_name)).date().isoformat())
    arguments = parser.parse_args()
    asyncio.run(sync(date.fromisoformat(arguments.date)))


if __name__ == "__main__":
    main()
