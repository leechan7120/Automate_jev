from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TOOL_NAMES = {"slack-search-messages", "slack_search_messages"}
SLACK_SEARCH_URL = "https://slack.com/api/search.messages"


def main() -> None:
    for raw_line in sys.stdin:
        try:
            request = json.loads(raw_line)
            response = _handle(request)
        except (json.JSONDecodeError, ValueError) as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": str(error)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def _handle(request: Mapping[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if not isinstance(method, str):
        raise ValueError("MCP method is required")
    if request_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "automate-jev-slack-token", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _result(request_id, {
            "tools": [{
                "name": "slack-search-messages",
                "description": "Search Slack messages using the configured Slack OAuth token.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "maxLength": 512}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }],
        })
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, Mapping):
            return _error(request_id, -32602, "MCP tool parameters are required")
        name = str(params.get("name", ""))
        if name not in TOOL_NAMES:
            return _error(request_id, -32601, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            return _error(request_id, -32602, "Tool arguments must be an object")
        query = str(arguments.get("query", "")).strip()
        if not query or len(query) > 512:
            return _error(request_id, -32602, "query must contain 1 to 512 characters")
        try:
            messages = _search_messages(query)
        except RuntimeError as error:
            return _result(request_id, {"isError": True, "content": [{"type": "text", "text": str(error)}]})
        payload = {"messages": messages}
        return _result(request_id, {
            "structuredContent": payload,
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        })
    return _error(request_id, -32601, f"Unknown MCP method: {method}")


def _search_messages(query: str) -> list[dict[str, str]]:
    token = os.environ.get("SLACK_OAUTH_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SLACK_OAUTH_TOKEN is not configured")
    request = Request(
        SLACK_SEARCH_URL,
        data=urlencode({"query": query, "count": "100"}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Slack API request failed: {type(error).__name__}") from None
    if not isinstance(body, Mapping) or body.get("ok") is not True:
        error_code = str(body.get("error", "unknown_error")) if isinstance(body, Mapping) else "invalid_response"
        raise RuntimeError(f"Slack API rejected the search: {error_code}")
    raw_matches = body.get("messages", {})
    matches = raw_matches.get("matches", []) if isinstance(raw_matches, Mapping) else []
    if not isinstance(matches, list):
        return []
    return [_normalize_message(item) for item in matches if isinstance(item, Mapping)]


def _normalize_message(item: Mapping[str, Any]) -> dict[str, str]:
    channel = item.get("channel_name", item.get("channel", ""))
    if isinstance(channel, Mapping):
        channel = channel.get("name", channel.get("id", ""))
    author = item.get("username", item.get("user_name", item.get("user", "")))
    return {
        "id": str(item.get("ts", item.get("id", ""))),
        "channel_name": str(channel),
        "user": str(author),
        "text": str(item.get("text", "")),
        "timestamp": str(item.get("ts", "")),
        "permalink": str(item.get("permalink", "")),
    }


def _result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
