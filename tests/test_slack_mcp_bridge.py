import json

from automate_jev.slack_mcp_bridge import _handle, _normalize_message


def test_bridge_advertises_search_tool():
    response = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})

    assert response["result"]["tools"][0]["name"] == "slack-search-messages"


def test_bridge_normalizes_slack_search_result():
    message = _normalize_message({
        "ts": "123.456",
        "channel_name": "engineering",
        "username": "chanh",
        "text": "API shipped",
    })

    assert message == {
        "id": "123.456",
        "channel_id": "",
        "channel_name": "engineering",
        "user": "chanh",
        "text": "API shipped",
        "timestamp": "123.456",
        "thread_ts": "",
        "permalink": "",
    }


def test_bridge_returns_mcp_error_without_token(monkeypatch):
    monkeypatch.delenv("SLACK_OAUTH_TOKEN", raising=False)
    response = _handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "slack-search-messages", "arguments": {"query": "after:2026-10-03"}},
    })

    assert response["result"]["isError"] is True
    assert "SLACK_OAUTH_TOKEN" in response["result"]["content"][0]["text"]
