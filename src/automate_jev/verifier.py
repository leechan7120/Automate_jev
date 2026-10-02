from __future__ import annotations

import asyncio
from enum import StrEnum
from time import monotonic
from typing import Awaitable, Callable, Iterable, Mapping, Any

from .models import Observation


class VerificationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


def effects_match(effects: Iterable[Mapping[str, Any]], observation: Observation) -> bool:
    for effect in effects:
        if effect.get("type") != "fact_equals":
            return False
        if observation.facts.get(str(effect.get("key"))) != effect.get("value"):
            return False
    return True


async def verify_until_stable(
    observe: Callable[[], Awaitable[Observation]],
    effects: tuple[Mapping[str, Any], ...],
    *,
    deadline_seconds: float = 5.0,
    schedule: tuple[float, ...] = (0.1, 0.2, 0.4, 0.8, 1.0),
) -> VerificationStatus:
    started = monotonic()
    stable_successes = 0
    index = 0
    while monotonic() - started < deadline_seconds:
        observation = await observe()
        if effects_match(effects, observation):
            stable_successes += 1
            if stable_successes >= 2:
                return VerificationStatus.SUCCESS
        else:
            stable_successes = 0
        delay = schedule[min(index, len(schedule) - 1)]
        index += 1
        await asyncio.sleep(min(delay, max(0, deadline_seconds - (monotonic() - started))))
    return VerificationStatus.UNKNOWN

