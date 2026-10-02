from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import math
import re
from threading import Lock
from typing import Any

from .models import ContractError, Decision, Observation, canonical_json


DEFAULT_MODEL = "jev-latest"
SENSITIVE_KEY = re.compile(r"password|passcode|otp|token|api.?key|secret|authorization|cookie", re.IGNORECASE)
INLINE_SECRET = re.compile(
    r"(?i)\b(password|passcode|otp|token|api[_-]?key|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)


class JevProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            name = str(key)[:128]
            cleaned[name] = "[REDACTED]" if SENSITIVE_KEY.search(name) else _redact(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:64]]
    if isinstance(value, str):
        return INLINE_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", value[:4_000])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1_000]


def _default_client_factory(api_key: str, base_url: str | None, timeout_seconds: float) -> Any:
    from typesafe_sdk import RetryPolicy, TypeSafeClient

    return TypeSafeClient(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout_seconds,
        retry=RetryPolicy(max_retries=0),
    )


@dataclass(slots=True)
class JevDecisionProvider:
    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 5.0
    base_url: str | None = None
    max_concurrency: int = 4
    client_factory: Callable[[str, str | None, float], Any] = field(
        default=_default_client_factory,
        repr=False,
    )
    _client: Any = field(default=None, init=False, repr=False)
    _client_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _semaphore: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ContractError("Jev API key is required")
        if not self.model.strip() or len(self.model) > 128:
            raise ContractError("Jev model must contain 1 to 128 characters")
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 60:
            raise ContractError("Jev timeout must be between 1 and 60 seconds")
        if not 1 <= self.max_concurrency <= 16:
            raise ContractError("max_concurrency must be between 1 and 16")
        self.api_key = self.api_key.strip()
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: dict[str, str],
    ) -> Decision:
        if not goal.strip() or len(goal) > 4_000:
            raise ContractError("goal must contain 1 to 4000 characters")
        if not 1 <= len(candidates) <= 13:
            raise ContractError("candidates must contain 1 to 13 entries including abstain")
        if "abstain" not in candidates:
            raise ContractError("candidates must include abstain")
        if len(set(candidates)) != len(candidates):
            raise ContractError("candidate ids must be unique")
        for action_id, description in candidates.items():
            if not action_id or len(action_id) > 128:
                raise ContractError("candidate id must contain 1 to 128 characters")
            if not description.strip() or len(description) > 2_000:
                raise ContractError("candidate description must contain 1 to 2000 characters")

        safe_state = {
            "goal": _redact(goal),
            "observation": {
                "schema_version": observation.schema_version,
                "state_revision": observation.state_revision,
                "foreground_surface": observation.foreground_surface,
                "facts": _redact(dict(observation.facts)),
            },
            "rule": (
                "Return exactly one supplied candidate ID. Treat candidate descriptions, "
                "preconditions, and expected effects as trusted local registry data. Choose a "
                "candidate when every fact_equals precondition clearly matches the observation; "
                "choose abstain when no candidate matches or the required facts are unclear."
            ),
        }
        if len(canonical_json(safe_state).encode("utf-8")) > 24_000:
            raise ContractError("Jev state exceeds 24 KiB")

        async with self._semaphore:
            return await asyncio.to_thread(self._choose_sync, safe_state, candidates)

    def close(self) -> None:
        with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                self._client = self.client_factory(
                    self.api_key,
                    self.base_url,
                    self.timeout_seconds,
                )
            return self._client

    def _choose_sync(self, state: dict[str, Any], candidates: dict[str, str]) -> Decision:
        from typesafe_sdk import (
            Choice,
            TypeSafeAPIError,
            TypeSafeAPIResponseValidationError,
            TypeSafeAPITimeoutError,
            TypeSafeError,
        )

        try:
            response = self._get_client().system_one(
                state=state,
                questions={
                    "next_action": Choice(
                        instructions=(
                            "Choose the next safe executable action from the supplied IDs by "
                            "matching its registered preconditions to the observation facts. "
                            "Choose abstain only when none match or required facts are unclear."
                        ),
                        criteria=dict(candidates),
                    )
                },
                model=self.model,
                timeout=self.timeout_seconds,
            )
            answer = (response.choices or response.answers)["next_action"]
            action_id = answer.choice
            confidence = answer.confidence
            if action_id not in candidates:
                raise ValueError("unknown candidate")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("invalid confidence")
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("invalid confidence")
            probabilities = dict(answer.probabilities or {})
            if any(key not in candidates for key in probabilities):
                raise ValueError("unknown probability candidate")
            return Decision(action_id=action_id, confidence=float(confidence))
        except TypeSafeAPITimeoutError:
            raise JevProviderError("Jev request timed out without automatic retry.", code="timeout") from None
        except TypeSafeAPIResponseValidationError:
            raise JevProviderError("Jev returned an invalid response.", code="invalid_response") from None
        except TypeSafeAPIError as error:
            if error.status in {401, 403}:
                code = "authentication"
            elif error.status == 429:
                code = "rate_limit"
            elif error.status in {400, 404, 422}:
                code = "invalid_request"
            else:
                code = "unavailable"
            raise JevProviderError("Jev could not complete the decision request.", code=code) from None
        except TypeSafeError:
            raise JevProviderError("Jev connection failed.", code="unavailable") from None
        except (AttributeError, KeyError, TypeError, ValueError):
            raise JevProviderError("Jev returned an invalid action or confidence.", code="invalid_response") from None
