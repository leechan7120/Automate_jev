from __future__ import annotations

import json
import sys
from typing import Any

from .models import digest_json


facts: dict[str, Any] = {"required_text_present": False}


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
    if action == "execute_registered":
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
