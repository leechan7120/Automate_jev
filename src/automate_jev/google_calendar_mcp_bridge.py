from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


TOOL_NAMES = {"calendar-create-event", "calendar_create_event"}
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


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
            "serverInfo": {"name": "automate-jev-google-calendar", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _result(request_id, {
            "tools": [{
                "name": "calendar-create-event",
                "description": "Create a confirmed event in Google Calendar.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "maxLength": 200},
                        "start_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                        "start_time": {"type": "string", "pattern": "^([0-9]{2}:[0-9]{2})?$"},
                        "end_date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
                        "end_time": {"type": "string", "pattern": "^([0-9]{2}:[0-9]{2})?$"},
                        "timezone": {"type": "string"},
                        "all_day": {"type": "boolean"},
                        "description": {"type": "string", "maxLength": 1000},
                        "source_message_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["summary", "start_date", "end_date", "timezone", "all_day"],
                    "additionalProperties": False,
                },
            }],
        })
    if method != "tools/call":
        return _error(request_id, -32601, f"Unknown MCP method: {method}")
    params = request.get("params")
    if not isinstance(params, Mapping):
        return _error(request_id, -32602, "MCP tool parameters are required")
    if str(params.get("name", "")) not in TOOL_NAMES:
        return _error(request_id, -32601, f"Unknown tool: {params.get('name', '')}")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, Mapping):
        return _error(request_id, -32602, "Tool arguments must be an object")
    try:
        event = _create_event(arguments)
    except (RuntimeError, ValueError) as error:
        return _result(request_id, {"isError": True, "content": [{"type": "text", "text": str(error)}]})
    payload = {"event": event}
    return _result(request_id, {
        "structuredContent": payload,
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
    })


def _create_event(arguments: Mapping[str, Any]) -> dict[str, Any]:
    summary = str(arguments.get("summary", "")).strip()
    start_date = str(arguments.get("start_date", "")).strip()
    end_date = str(arguments.get("end_date", start_date)).strip()
    timezone_name = str(arguments.get("timezone", "UTC")).strip() or "UTC"
    all_day = bool(arguments.get("all_day", False))
    if not summary or len(summary) > 200:
        raise ValueError("summary must contain 1 to 200 characters")
    try:
        date.fromisoformat(start_date)
        date.fromisoformat(end_date)
    except ValueError as error:
        raise ValueError("start_date and end_date must use YYYY-MM-DD") from error
    if all_day:
        end_exclusive = date.fromisoformat(end_date) + timedelta(days=1)
        body = {
            "summary": summary,
            "description": str(arguments.get("description", ""))[:1000],
            "start": {"date": start_date},
            "end": {"date": end_exclusive.isoformat()},
        }
    else:
        start_time = str(arguments.get("start_time", "")).strip()
        end_time = str(arguments.get("end_time", "")).strip()
        if not start_time or not end_time:
            raise ValueError("timed events require start_time and end_time")
        try:
            start = datetime.fromisoformat(f"{start_date}T{start_time}")
            end = datetime.fromisoformat(f"{end_date}T{end_time}")
        except ValueError as error:
            raise ValueError("event times must use HH:MM") from error
        if end <= start:
            raise ValueError("event end must be after event start")
        body = {
            "summary": summary,
            "description": str(arguments.get("description", ""))[:1000],
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        }
    service = _calendar_service()
    result = service.events().insert(
        calendarId=os.environ.get("GOOGLE_CALENDAR_ID", "primary"),
        body=body,
        sendUpdates="all",
    ).execute()
    return {
        "id": str(result.get("id", "")),
        "html_link": str(result.get("htmlLink", "")),
        "summary": summary,
        "status": str(result.get("status", "confirmed")),
    }


def _calendar_service() -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError("Google Calendar dependencies are not installed") from error
    token_path = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN_FILE", "/app/.automate-jev/google-calendar-token.json"))
    credentials_path = Path(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE", "/app/.automate-jev/google-calendar-credentials.json"))
    if not token_path.exists():
        raise RuntimeError(
            f"Google Calendar OAuth token is missing: {token_path}. "
            "Run automate-jev-google-calendar-auth first."
        )
    credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        raise RuntimeError("Google Calendar OAuth token is invalid or expired")
    if not credentials_path.exists():
        raise RuntimeError(f"Google Calendar credentials file is missing: {credentials_path}")
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()