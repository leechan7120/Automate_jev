import pytest

from automate_jev.memory import LocalMemoryStore
from automate_jev.notion import NotionMCP


class FakeMCP:
    def __init__(self):
        self.calls = []

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        if tool == "notion-get-tool-access":
            return {"current_tool_access": {"search": {"status": "available"}}}
        if tool == "notion-search":
            return {
                "results": [{
                    "id": "page-1",
                    "properties": {"title": {"title": [{"plain_text": "Morning review"}]}},
                    "text": "Review calendar and prepare the daily plan.",
                    "last_edited_time": "2026-10-01T09:00:00Z",
                }]
            }
        return {"created": True}


@pytest.mark.asyncio
async def test_notion_search_is_consolidated_into_local_memory(tmp_path):
    client = FakeMCP()
    notion = NotionMCP(client)

    records = await notion.sync_to_memory("daily plan", LocalMemoryStore(tmp_path))

    assert records[0].scope == "notion-page-1"
    assert "Morning review" in (tmp_path / "semantic" / "notion-page-1.md").read_text()
    assert client.calls[0] == ("notion", "notion-get-tool-access", {})
    assert client.calls[1] == ("notion", "notion-search", {"query": "daily plan"})


@pytest.mark.asyncio
async def test_notion_routine_publication_uses_configured_parent():
    client = FakeMCP()
    result = await NotionMCP(client).publish_routine(
        title="Daily review routine",
        content="Run the calendar review every weekday morning.",
        parent_id="database-1",
    )

    assert result == {"created": True}
    assert client.calls[-1] == (
        "notion",
        "notion-create-pages",
        {
            "allow_async": False,
            "parent": {"page_id": "database-1"},
            "pages": [{
                "properties": {"title": "Daily review routine"},
                "content": "Run the calendar review every weekday morning.",
            }],
        },
    )


@pytest.mark.asyncio
async def test_notion_page_update_replaces_content():
    client = FakeMCP()

    result = await NotionMCP(client).update_page(
        page_id="page-1",
        title="Project Root",
        content="# Updated project overview",
    )

    assert result == {"created": True}
    assert client.calls[-1] == (
        "notion",
        "notion-update-page",
        {
            "allow_async": False,
            "page_id": "page-1",
            "command": "replace_content",
            "new_str": "# Updated project overview",
        },
    )


@pytest.mark.asyncio
async def test_notion_fetch_accepts_single_structured_page():
    class FetchClient:
        async def call_tool(self, *, server, tool, arguments):
            return {
                "structuredContent": {
                    "id": "page-1",
                    "title": "Project Root",
                    "text": "# Project overview",
                },
            }

    page = await NotionMCP(FetchClient()).fetch("page-1")

    assert page.page_id == "page-1"
    assert page.title == "Project Root"
    assert page.text == "# Project overview"