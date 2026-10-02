from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import asyncio
import ctypes
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .models import Action, ContractError, Risk, canonical_json
from .routine import AutonomousRoutineRunner, RoutineCadence, RoutinePattern


_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ObservedActivity:
    """A locally collected activity snapshot; it does not contain an action id."""

    observed_at: datetime
    surface: str
    topic: str
    foreground: str
    duration_seconds: int = 0
    facts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ContractError("activity timestamp must include a timezone")
        for value, name, maximum in (
            (self.surface, "activity surface", 128),
            (self.topic, "activity topic", 256),
            (self.foreground, "activity foreground", 256),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ContractError(f"{name} is invalid")
        if isinstance(self.duration_seconds, bool) or not 0 <= self.duration_seconds <= 86_400:
            raise ContractError("activity duration must be between 0 and 86400 seconds")
        if len(self.facts) > 32:
            raise ContractError("activity facts must contain at most 32 values")

    @property
    def fingerprint(self) -> tuple[str, str, int]:
        normalized_topic = _SPACE.sub(" ", self.topic.strip().lower())
        return self.surface.strip().lower(), normalized_topic, self.observed_at.hour

    def payload(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "surface": self.surface,
            "topic": self.topic,
            "foreground": self.foreground,
            "duration_seconds": self.duration_seconds,
            "facts": dict(self.facts),
        }


class ActivitySource(Protocol):
    async def current_activity(self) -> ObservedActivity | None: ...


@dataclass(slots=True)
class WindowsForegroundActivitySource:
    """Passively samples the foreground Windows application without an action id."""

    async def current_activity(self) -> ObservedActivity | None:
        if os.name != "nt":
            raise ContractError("WindowsForegroundActivitySource requires Windows")
        return await asyncio.to_thread(self._read_current)

    @staticmethod
    def _read_current() -> ObservedActivity | None:
        import ctypes.wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        window = user32.GetForegroundWindow()
        if not window:
            return None
        title_buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(window, title_buffer, len(title_buffer))
        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        process_name = "windows"
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if handle:
            try:
                path_buffer = ctypes.create_unicode_buffer(512)
                path_length = ctypes.wintypes.DWORD(len(path_buffer))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(path_length)):
                    process_name = Path(path_buffer.value).stem or process_name
            finally:
                kernel32.CloseHandle(handle)
        title = title_buffer.value.strip() or process_name
        return ObservedActivity(
            observed_at=datetime.now(timezone.utc),
            surface=process_name,
            topic=title,
            foreground=f"{process_name}: {title}",
        )


@dataclass(slots=True)
class LocalActivityStore:
    """Append-only local activity memory. Raw page contents are never required."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, activity: ObservedActivity) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(activity.payload()) + "\n")

    def read(self) -> tuple[ObservedActivity, ...]:
        if not self.path.exists():
            return ()
        activities: list[ObservedActivity] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
                activities.append(
                    ObservedActivity(
                        observed_at=datetime.fromisoformat(str(payload["observed_at"])),
                        surface=str(payload["surface"]),
                        topic=str(payload["topic"]),
                        foreground=str(payload["foreground"]),
                        duration_seconds=int(payload.get("duration_seconds", 0)),
                        facts=dict(payload.get("facts", {})),
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ContractError("activity memory contains an invalid record") from error
        return tuple(activities)


@dataclass(slots=True)
class PassiveActivityMonitor:
    source: ActivitySource
    store: LocalActivityStore
    _last_fingerprint: tuple[str, str, int] | None = field(default=None, init=False)

    async def sample(self) -> ObservedActivity | None:
        activity = await self.source.current_activity()
        if activity is None or activity.fingerprint == self._last_fingerprint:
            return None
        self._last_fingerprint = activity.fingerprint
        self.store.append(activity)
        return activity


@dataclass(slots=True)
class PassiveAutomationService:
    """One background tick: observe, relearn from local history, then run due routines."""

    monitor: PassiveActivityMonitor
    learner: "PassiveRoutineLearner"
    actions: tuple[Action, ...]
    runner: AutonomousRoutineRunner
    memory_sync: Any | None = None
    memory_store: Any | None = None
    memory_query: str = "routine"

    async def tick(self, *, now: datetime | None = None):
        current = await self.monitor.sample()
        if self.memory_sync is not None and self.memory_store is not None:
            await self.memory_sync.sync_to_memory(self.memory_query, self.memory_store)
        patterns = self.learner.learn(self.monitor.store.read(), self.actions)
        check_at = now or datetime.now(timezone.utc)
        results = await self.runner.run_due(patterns, now=check_at)
        return current, patterns, results


@dataclass(slots=True)
class PassiveRoutineLearner:
    minimum_occurrences: int = 5
    interval_tolerance: float = 0.2

    def __post_init__(self) -> None:
        if self.minimum_occurrences < 5:
            raise ContractError("passive routine learning requires at least 5 observations")
        if not 0 < self.interval_tolerance < 0.5:
            raise ContractError("interval_tolerance must be between 0 and 0.5")

    def learn(
        self,
        activities: Iterable[ObservedActivity],
        actions: Iterable[Action],
    ) -> tuple[RoutinePattern, ...]:
        action_list = tuple(actions)
        groups: dict[tuple[str, str, int], list[ObservedActivity]] = defaultdict(list)
        for activity in activities:
            groups[activity.fingerprint].append(activity)

        patterns: list[RoutinePattern] = []
        for fingerprint, samples in groups.items():
            ordered = sorted(samples, key=lambda sample: sample.observed_at)
            if len(ordered) < self.minimum_occurrences:
                continue
            match = self._match_action(ordered[0], action_list)
            if match is None:
                continue
            intervals = [
                (current.observed_at - previous.observed_at).total_seconds()
                for previous, current in zip(ordered, ordered[1:])
            ]
            cadence, period = self._cadence(intervals)
            if cadence is None:
                continue
            average = sum(intervals) / len(intervals)
            consistency = 1 - min(1, sum(abs(value - average) / average for value in intervals) / len(intervals))
            confidence = min(1.0, 0.5 * consistency + 0.5 * min(1.0, len(ordered) / 10))
            last_seen = ordered[-1].observed_at
            patterns.append(
                RoutinePattern(
                    action_id=match.id,
                    context=(
                        ("surface", fingerprint[0]),
                        ("topic", fingerprint[1]),
                        ("hour", str(fingerprint[2])),
                    ),
                    cadence=cadence,
                    occurrences=len(ordered),
                    success_rate=1.0,
                    confidence=confidence,
                    last_seen_at=last_seen,
                    next_due_at=last_seen + timedelta(seconds=period),
                    risk=match.risk,
                )
            )
        return tuple(sorted(patterns, key=lambda pattern: pattern.routine_id))

    @staticmethod
    def _match_action(activity: ObservedActivity, actions: tuple[Action, ...]) -> Action | None:
        surface = activity.surface.lower()
        candidates = [
            action
            for action in actions
            if action.domain.lower() in {surface, f"mcp.{surface}"}
            or str(action.target.get("mcp_server", "")).lower() == surface
        ]
        if len(candidates) == 1:
            return candidates[0]
        topic_words = set(_SPACE.sub(" ", activity.topic.lower()).split())
        scored = sorted(
            candidates,
            key=lambda action: len(topic_words & set(_SPACE.sub(" ", f"{action.id} {action.description}".lower()).split())),
            reverse=True,
        )
        return scored[0] if scored and (len(scored) == 1 or scored[0] != scored[1]) else None

    def _cadence(self, intervals: list[float]) -> tuple[RoutineCadence | None, float]:
        average = sum(intervals) / len(intervals)
        candidates = (
            (RoutineCadence.DAILY, 86_400.0),
            (RoutineCadence.WEEKLY, 604_800.0),
            (RoutineCadence.MONTHLY, 2_592_000.0),
        )
        cadence, period = min(candidates, key=lambda item: abs(average - item[1]))
        return (cadence, period) if abs(average - period) / period <= self.interval_tolerance else (None, 0)