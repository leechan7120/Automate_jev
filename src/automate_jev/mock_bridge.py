from __future__ import annotations

import json
import sys
from typing import Any

from .models import digest_json


facts: dict[str, Any] = {"required_text_present": False}
registry: dict[str, Any] = {"session_id": None, "version": 0, "actions": {}}
executed: set[str] = set()


def observation() -> dict[str, Any]:
    stable = {"foreground_surface": "desktop", "facts": facts}
    return {
        "schema_version": "1.0",
        "state_revision": digest_json(stable),
        "foreground_surface": "desktop",
        "facts": dict(facts),
    }


def handle(request: dict[str, Any]) -> dict[str, Any]:
    request_id = request.get("id")
    action = request.get("action")
    payload = request.get("payload") or {}
    if action == "observe":
        return {"id": request_id, "ok": True, "result": observation()}
    if action == "register_actions":
        actions = payload.get("actions") or []
        session_id = payload.get("session_id")
        version = payload.get("registry_version")
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or type(version) is not int
            or version < 1
            or not isinstance(actions, list)
            or not 1 <= len(actions) <= 12
        ):
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "MALFORMED_REQUEST", "message": "invalid registry"},
            }
        next_actions: dict[str, str] = {}
        for item in actions:
            if not isinstance(item, dict):
                return {
                    "id": request_id,
                    "ok": False,
                    "error": {"code": "MALFORMED_REQUEST", "message": "invalid action"},
                }
            action_id = item.get("id")
            action_hash = item.get("action_hash")
            if (
                not isinstance(action_id, str)
                or not action_id.strip()
                or not isinstance(action_hash, str)
                or not action_hash.startswith("sha256:")
                or action_id in next_actions
            ):
                return {
                    "id": request_id,
                    "ok": False,
                    "error": {"code": "MALFORMED_REQUEST", "message": "invalid action"},
                }
            next_actions[action_id] = action_hash
        same_session = session_id == registry["session_id"]
        if same_session and version <= registry["version"]:
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "INVALID_ACTION", "message": "registry version must increase"},
            }
        if not same_session:
            executed.clear()
        registry["session_id"] = session_id
        registry["version"] = version
        registry["actions"] = next_actions
        return {"id": request_id, "ok": True, "result": {"registered": len(actions)}}
    if action == "execute_registered":
        if (
            not payload.get("idempotency_key")
            or payload.get("session_id") != registry["session_id"]
            or payload.get("registry_version") != registry["version"]
            or registry["actions"].get(payload.get("action_id")) != payload.get("action_hash")
        ):
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "INVALID_ACTION", "message": "action is not registered"},
            }
        if payload.get("idempotency_key") in executed:
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "DUPLICATE", "message": "already executed"},
            }
        if payload.get("expected_state_revision") != observation()["state_revision"]:
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "STALE_ACTION", "message": "state revision changed"},
            }
        for effect in payload.get("expected_effects") or []:
            if effect.get("type") != "fact_equals":
                return {
                    "id": request_id,
                    "ok": False,
                    "error": {"code": "INVALID_ACTION", "message": "unsupported effect"},
                }
            facts[str(effect["key"])] = effect.get("value")
        executed.add(payload.get("idempotency_key"))
        return {"id": request_id, "ok": True, "result": {"execution_state": "confirmed"}}
    return {
        "id": request_id,
        "ok": False,
        "error": {"code": "MALFORMED_REQUEST", "message": "unsupported action"},
    }


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle(request)
        except Exception:
            response = {
                "id": None,
                "ok": False,
                "error": {"code": "MALFORMED_REQUEST", "message": "invalid request"},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
