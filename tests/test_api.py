from fastapi.testclient import TestClient

from automate_jev.api import create_app


class NotionMCPStub:
    def __init__(self):
        self.calls = []

    async def call_tool(self, *, server, tool, arguments):
        self.calls.append((server, tool, dict(arguments)))
        if tool == "notion-get-tool-access":
            return {"current_tool_access": {"search": {"status": "available"}}}
        if tool == "notion-search":
            return {"results": [{"id": "page-1", "title": "Daily routine", "text": "Review calendar"}]}
        if tool == "notion-create-pages":
            return {"results": [{"id": "routine-page-1"}]}
        raise AssertionError(tool)


async def successful_runner(workflow, provider, session_id):
    assert workflow.workflow_id == "fixture-upload"
    return {
        "provider": provider.upper(),
        "status": "SUCCESS",
        "action_id": workflow.steps[0].action_id,
        "effect_observed": True,
        "journal_state": "COMMITTED",
        "session_id": session_id,
    }


def test_health_and_notion_mcp_sync(tmp_path):
    client = TestClient(
        create_app(
            env_path=tmp_path / "missing.env",
            mcp_client=NotionMCPStub(),
            memory_root=tmp_path / "memory",
        )
    )
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/").text
    assert "MCP" in page

    response = client.post("/v1/mcp/notion/sync", json={"query": "daily routine"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "notion-mcp"
    assert body["records"][0]["text"] == "Daily routine\nReview calendar"
    assert (tmp_path / "memory" / "semantic" / "notion-page-1.md").exists()


def test_mcp_sync_rejects_invalid_query_and_missing_client(tmp_path):
    client = TestClient(create_app(env_path=tmp_path / "missing.env"))
    assert client.post("/v1/mcp/notion/sync", json={"query": ""}).status_code == 400
    assert client.post("/v1/mcp/notion/sync", json={"query": "daily routine"}).status_code == 503


def test_notion_routine_publish_requires_explicit_enablement(tmp_path, monkeypatch):
    stub = NotionMCPStub()
    monkeypatch.setenv("NOTION_ROUTINE_PARENT_ID", "routines-page")
    monkeypatch.setenv("NOTION_PUBLISH_ENABLED", "true")
    client = TestClient(create_app(mcp_client=stub, memory_root=tmp_path / "memory"))

    response = client.post(
        "/v1/mcp/notion/publish-routine",
        json={"title": "Morning routine", "content": "Review the calendar."},
    )

    assert response.status_code == 200
    assert response.json()["parent_id"] == "routines-page"
    assert stub.calls[-1] == (
        "notion",
        "notion-create-pages",
        {
            "allow_async": False,
            "parent": {"page_id": "routines-page"},
            "pages": [{
                "properties": {"title": "Morning routine"},
                "content": "Review the calendar.",
            }],
        },
    )


def test_review_token_exchanges_once_for_successful_execution(tmp_path):
    client = TestClient(
        create_app(
            env_path=tmp_path / "missing.env",
            execution_runner=successful_runner,
            journal_root=tmp_path / "journal",
        )
    )
    workflow = {
        "schema_version": "1.0",
        "workflow_id": "fixture-upload",
        "allowed_root": "D:/compe/skku_AI_hack/demo-data",
        "steps": [{"id": "step-1", "domain": "desktop", "action_id": "smoke-target.fill-required-text", "success": {"required_text_present": True}, "risk": "safe"}],
        "completion": {"all": [{"required_text_present": True}]},
    }
    review = client.post("/v1/workflows/review", json=workflow).json()["review"]

    response = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )

    assert response.status_code == 202
    session_id = response.json()["session_id"]
    status = client.get(f"/v1/executions/{session_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "SUCCESS"
    assert status.json()["result"]["journal_state"] == "COMMITTED"

    replay = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )
    assert replay.status_code == 400
    assert "already been exchanged" in replay.json()["detail"]


def test_execution_rejects_workflow_changed_after_review(tmp_path):
    client = TestClient(create_app(execution_runner=successful_runner))
    workflow = {
        "schema_version": "1.0",
        "workflow_id": "fixture-upload",
        "allowed_root": "D:/demo",
        "steps": [{"id": "step-1", "domain": "desktop", "action_id": "smoke-target.fill-required-text", "success": {"required_text_present": True}, "risk": "safe"}],
        "completion": {"all": [{"required_text_present": True}]},
    }
    review = client.post("/v1/workflows/review", json=workflow).json()["review"]
    workflow["allowed_root"] = "D:/changed"

    response = client.post(
        "/v1/executions",
        json={"workflow": workflow, "review": review, "provider": "fixture"},
    )

    assert response.status_code == 400
    assert "hash mismatch" in response.json()["detail"]


