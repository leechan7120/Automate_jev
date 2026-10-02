from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Awaitable, Callable, Iterable, Mapping

from .models import Action, ContractError, Risk
from .orchestrator import RunResult, RunStatus


class RoutineCadence(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    action_id: str
    occurred_at: datetime
    succeeded: bool = True
    context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_id.strip() or len(self.action_id) > 128:
            raise ContractError("usage event action_id must contain 1 to 128 characters")
        if self.occurred_at.tzinfo is None:
            raise ContractError("usage event timestamp must include a timezone")
        if not isinstance(self.succeeded, bool):
            raise ContractError("usage event succeeded must be boolean")
        if len(self.context) > 32:
            raise ContractError("usage event context must contain at most 32 values")

    @property
    def key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return self.action_id, tuple(sorted((str(key), str(value)) for key, value in self.context.items()))


@dataclass(frozen=True, slots=True)
class RoutinePattern:
    action_id: str
    context: tuple[tuple[str, str], ...]
    cadence: RoutineCadence
    occurrences: int
    success_rate: float
    confidence: float
    last_seen_at: datetime
    next_due_at: datetime
    risk: Risk

    @property
    def auto_execute(self) -> bool:
        return self.risk is Risk.SAFE and self.occurrences >= 3 and self.success_rate >= 0.9 and self.confidence >= 0.8

    @property
    def routine_id(self) -> str:
        suffix = ",".join(f"{key}={value}" for key, value in self.context)
        return f"{self.action_id}|{suffix}" if suffix else self.action_id

    def is_due(self, now: datetime) -> bool:
        if now.tzinfo is None:
            raise ContractError("routine check timestamp must include a timezone")
        return now >= self.next_due_at


@dataclass(slots=True)
class RoutineLearner:
    minimum_occurrences: int = 3
    interval_tolerance: float = 0.25

    def __post_init__(self) -> None:
        if self.minimum_occurrences < 3:
            raise ContractError("minimum_occurrences must be at least 3")
        if not 0 < self.interval_tolerance < 0.5:
            raise ContractError("interval_tolerance must be between 0 and 0.5")

    def learn(self, events: Iterable[UsageEvent], actions: Iterable[Action]) -> tuple[RoutinePattern, ...]:
        action_risks = {action.id: action.risk for action in actions}
        grouped: dict[tuple[str, tuple[tuple[str, str], ...]], list[UsageEvent]] = defaultdict(list)
        for event in events:
            if event.action_id in action_risks:
                grouped[event.key].append(event)

        patterns: list[RoutinePattern] = []
        for key, grouped_events in grouped.items():
            ordered = sorted(grouped_events, key=lambda event: event.occurred_at)
            if len(ordered) < self.minimum_occurrences:
                continue
            intervals = [
                (current.occurred_at - previous.occurred_at).total_seconds()
                for previous, current in zip(ordered, ordered[1:])
            ]
            cadence, expected_seconds = self._cadence(intervals)
            if cadence is None:
                continue
            average_interval = sum(intervals) / len(intervals)
            consistency = 1 - min(1, self._mean_deviation(intervals, average_interval))
            success_rate = sum(event.succeeded for event in ordered) / len(ordered)
            confidence = max(0.0, min(1.0, 0.5 * consistency + 0.5 * min(1.0, len(ordered) / 6)))
            last_seen = ordered[-1].occurred_at
            patterns.append(
                RoutinePattern(
                    action_id=key[0],
                    context=key[1],
                    cadence=cadence,
                    occurrences=len(ordered),
                    success_rate=success_rate,
                    confidence=confidence,
                    last_seen_at=last_seen,
                    next_due_at=last_seen + timedelta(seconds=expected_seconds),
                    risk=action_risks[key[0]],
                )
            )
        return tuple(sorted(patterns, key=lambda pattern: pattern.routine_id))

    def _cadence(self, intervals: list[float]) -> tuple[RoutineCadence | None, float]:
        average = sum(intervals) / len(intervals)
        candidates = (
            (RoutineCadence.DAILY, 86400.0),
            (RoutineCadence.WEEKLY, 604800.0),
            (RoutineCadence.MONTHLY, 2_592_000.0),
        )
        cadence, expected = min(candidates, key=lambda candidate: abs(average - candidate[1]))
        if abs(average - expected) / expected > self.interval_tolerance:
            return None, 0
        return cadence, expected

    @staticmethod
    def _mean_deviation(intervals: list[float], average: float) -> float:
        if average <= 0:
            return 1.0
        return sum(abs(interval - average) / average for interval in intervals) / len(intervals)


ExecuteRoutine = Callable[[RoutinePattern, str], Awaitable[RunResult]]


@dataclass(slots=True)
class AutonomousRoutineRunner:
    execute: ExecuteRoutine

    async def run_due(self, patterns: Iterable[RoutinePattern], *, now: datetime) -> tuple[RunResult, ...]:
        if now.tzinfo is None:
            raise ContractError("routine run timestamp must include a timezone")
        results: list[RunResult] = []
        for pattern in patterns:
            if not pattern.is_due(now):
                continue
            if not pattern.auto_execute:
                results.append(
                    RunResult(
                        RunStatus.NEEDS_APPROVAL if pattern.risk is Risk.CONFIRM else RunStatus.ABSTAINED,
                        detail=f"routine {pattern.routine_id} is not eligible for autonomous execution",
                    )
                )
                continue
            results.append(await self.execute(pattern, f"routine:{pattern.routine_id}:{pattern.next_due_at.isoformat()}"))
        return tuple(results)