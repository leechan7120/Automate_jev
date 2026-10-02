from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import ContractError, canonical_json


MAX_MESSAGE_BYTES = 65_536
ALLOWED_ERROR_CODES = {
    "DUPLICATE",
    "EXPIRED",
    "INVALID_ACTION",
    "MALFORMED_REQUEST",
    "POLICY_BLOCKED",
    "STALE_ACTION",
    "TARGET_MISMATCH",
    "UNAVAILABLE",
}


class BridgeError(RuntimeError):
    def __init__(self, message: str, *, code: str = "UNAVAILABLE") -> None:
        super().__init__(message)
        self.code = code


def _minimal_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    allowed = ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    if extra:
        environment.update({str(key): str(value) for key, value in extra.items()})
    for key in tuple(environment):
        if any(marker in key.upper() for marker in ("TOKEN", "SECRET", "API_KEY", "PASSWORD")):
            environment.pop(key, None)
    return environment


@dataclass(slots=True)
class JsonLinesBridgeClient:
    command: Sequence[str]
    timeout_seconds: float = 5.0
    cwd: Path | None = None
    environment: Mapping[str, str] | None = None
    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise ContractError("bridge command must contain non-empty strings")
        if not 0.1 <= self.timeout_seconds <= 30:
            raise ContractError("bridge timeout must be between 0.1 and 30 seconds")

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self.cwd) if self.cwd else None,
            env=_minimal_environment(self.environment),
            limit=MAX_MESSAGE_BYTES,
        )

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def call(self, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not action or len(action) > 128:
            raise ContractError("bridge action must contain 1 to 128 characters")
        async with self._lock:
            await self.start()
            process = self._process
            assert process is not None and process.stdin is not None and process.stdout is not None
            if process.returncode is not None:
                raise BridgeError("Windows Host is not running")
            self._request_id += 1
            request = {"id": self._request_id, "action": action, "payload": dict(payload or {})}
            encoded = (canonical_json(request) + "\n").encode("utf-8")
            if len(encoded) > MAX_MESSAGE_BYTES:
                raise ContractError("bridge request exceeds 64 KiB")
            try:
                process.stdin.write(encoded)
                await asyncio.wait_for(process.stdin.drain(), timeout=self.timeout_seconds)
                line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds)
            except (TimeoutError, ConnectionError, BrokenPipeError):
                await self.close()
                raise BridgeError("Windows Host did not return a bounded response") from None
            if not line:
                await self.close()
                raise BridgeError("Windows Host exited without a response")
            if len(line) > MAX_MESSAGE_BYTES:
                await self.close()
                raise BridgeError("Windows Host response exceeds 64 KiB", code="MALFORMED_REQUEST")
            try:
                response = json.loads(line)
                if not isinstance(response, dict) or response.get("id") != self._request_id:
                    raise ValueError("response id mismatch")
                if not isinstance(response.get("ok"), bool):
                    raise ValueError("missing ok")
            except (json.JSONDecodeError, ValueError, TypeError):
                await self.close()
                raise BridgeError("Windows Host returned malformed JSON", code="MALFORMED_REQUEST") from None
            if not response["ok"]:
                error = response.get("error") or {}
                code = str(error.get("code") or "UNAVAILABLE")
                if code not in ALLOWED_ERROR_CODES:
                    code = "UNAVAILABLE"
                raise BridgeError("Windows Host rejected the request", code=code)
            result = response.get("result")
            if not isinstance(result, dict):
                await self.close()
                raise BridgeError("Windows Host result is malformed", code="MALFORMED_REQUEST")
            return result
