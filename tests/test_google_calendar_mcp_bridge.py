import json

from automate_jev.google_calendar_mcp_bridge import _handle


def test_calendar_bridge_exposes_create_event_tool():
    response = _handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    tools = response["result"]["tools"]
    assert tools[0]["name"] == "calendar-create-event"
    assert "start_date" in tools[0]["inputSchema"]["properties"]


def test_calendar_bridge_rejects_timed_event_without_end_time(monkeypatch):
    response = _handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "calendar-create-event",
            "arguments": {
                "summary": "Confirmed meeting",
                "start_date": "2026-10-07",
                "start_time": "14:00",
                "end_date": "2026-10-07",
                "end_time": "",
                "timezone": "Asia/Seoul",
                "all_day": False,
            },
        },
    })

    assert response["result"]["isError"] is True
    assert "require start_time and end_time" in response["result"]["content"][0]["text"]
