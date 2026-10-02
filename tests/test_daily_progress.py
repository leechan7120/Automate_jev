from datetime import date

import pytest

from automate_jev.daily_progress import DailyProgressService
from automate_jev.memory import LocalMemoryStore
from automate_jev.models import ContractError
from automate_jev.notion import NotionMCP
from automate_jev.progress_summarizer import ProjectSummary, ProgressSummary
from automate_jev.slack import SlackMCP, SlackMessage, _belongs_to_day
from automate_jev.daily_progress import _format_progress, _page_id


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        if server == "slack":
            return {"messages": [{"id": "m-1", "channel_name": "eng", "user": "chanh", "text": "Shipped the API integration."}]}
        if tool in {"notion-create-pages", "notion-update-page"}:
            return {"created": True, "id": "daily-page-1"}
        if tool == "notion-fetch":
            return {"results": [{"id": "root-page", "title": "Project Root", "text": "# Project overview"}]}
        raise AssertionError((server, tool))


class StaticSummarizer:
    async def summarize(self, *, messages, existing_root=""):
        return ProgressSummary(
            progress=("API shipped",),
            discussions=("Reviewed rollout",),
            decisions=("Approved release",),
            project_overview="Project overview",
            implementation_approach="MCP collection with Gemini organization",
            projects=(ProjectSummary(
                name="Automate Jev",
                overview="Project overview",
                progress=("API shipped",),
                discussions=("Reviewed rollout",),
                decisions=("Approved release",),
                next_actions=("Deploy worker",),
            ),),
        )


@pytest.mark.asyncio
async def test_daily_progress_is_saved_and_published(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        publish_enabled=True,
        summarizer=StaticSummarizer(),
    )

    progress = await service.collect_and_publish(date(2026, 10, 3))

    assert progress.messages[0].text == "Shipped the API integration."
    assert "API shipped" in progress.content
    assert "Shipped the API integration." not in progress.notion_result["content"] if "content" in progress.notion_result else True
    assert (tmp_path / "episodic" / "slack-daily-2026-10-03.md").exists()
    assert client.calls[-1][0:2] == ("notion", "notion-create-pages")
    assert client.calls[-1][2]["parent"] == {"page_id": "daily-progress-page"}


@pytest.mark.asyncio
async def test_daily_progress_does_not_publish_by_default(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
    )

    await service.collect_and_publish(date(2026, 10, 3))

    assert all(tool != "notion-create-pages" for _, tool, _ in client.calls)


@pytest.mark.asyncio
async def test_daily_progress_does_not_create_duplicate_page_after_restart(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        publish_enabled=True,
        summarizer=StaticSummarizer(),
    )

    await service.collect_and_publish(date(2026, 10, 3))
    await service.collect_and_publish(date(2026, 10, 3))

    assert [tool for _, tool, _ in client.calls].count("notion-create-pages") == 1
    assert [tool for _, tool, _ in client.calls].count("notion-update-page") == 1


@pytest.mark.asyncio
async def test_daily_progress_updates_project_root(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        project_root_page_id="root-page",
        publish_enabled=True,
        summarizer=StaticSummarizer(),
    )

    await service.collect_and_publish(date(2026, 10, 3))

    root_updates = [arguments for server, tool, arguments in client.calls if tool == "notion-update-page"]
    assert len(root_updates) == 1
    assert "# Project Root" in root_updates[0]["new_str"]
    assert "Current project overview" in root_updates[0]["new_str"]


def test_page_id_accepts_json_text_content_response():
    assert _page_id({
        "content": [{"type": "text", "text": '{"page_id":"daily-page-1"}'}],
    }) == "daily-page-1"


def test_slack_timestamp_is_filtered_in_configured_timezone():
    message = SlackMessage("m-1", "eng", "chanh", "Korean-timezone message", "1791028800.000000")

    assert _belongs_to_day(message, date(2026, 10, 3), "Asia/Seoul")


def test_notion_progress_content_omits_raw_evidence():
    content = _format_progress(
        date(2026, 10, 3),
        (),
        1,
        ProgressSummary(progress=("API shipped",), discussions=(), decisions=()),
        include_evidence=False,
    )

    assert "## Progress" in content
    assert "## Evidence" not in content


@pytest.mark.asyncio
async def test_daily_progress_creates_project_page_from_slack_summary(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        project_root_page_id="root-page",
        publish_enabled=True,
        summarizer=StaticSummarizer(),
    )

    progress = await service.collect_and_publish(date(2026, 10, 3))

    creates = [arguments for server, tool, arguments in client.calls if tool == "notion-create-pages"]
    assert len(creates) == 2
    assert creates[1]["parent"] == {"page_id": "root-page"}
    assert "Deploy worker" in creates[1]["pages"][0]["content"]
    assert progress.notion_result["projects"][0]["name"] == "Automate Jev"


@pytest.mark.asyncio
async def test_daily_progress_replaces_project_root_with_current_summary(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        project_root_page_id="root-page",
        publish_enabled=True,
        summarizer=StaticSummarizer(),
    )

    await service.collect_and_publish(date(2026, 10, 3))

    root_update = next(
        arguments for server, tool, arguments in client.calls if tool == "notion-update-page" and arguments["page_id"] == "root-page"
    )
    assert "# Project Root" in root_update["new_str"]
    assert "Project overview" in root_update["new_str"]
    assert "MCP collection with Gemini organization" in root_update["new_str"]
    assert "2026-10-03 (v1)" not in root_update["new_str"]


@pytest.mark.asyncio
async def test_daily_progress_never_publishes_raw_slack_without_llm(tmp_path):
    client = FakeMCP()
    service = DailyProgressService(
        slack=SlackMCP(client),
        notion=NotionMCP(client),
        memory=LocalMemoryStore(tmp_path),
        notion_parent_id="daily-progress-page",
        publish_enabled=True,
    )

    with pytest.raises(ContractError, match="LLM summarizer is required"):
        await service.collect_and_publish(date(2026, 10, 3))

    assert all(tool != "notion-create-pages" for _, tool, _ in client.calls)
