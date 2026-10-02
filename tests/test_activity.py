from datetime import datetime, timedelta, timezone

import pytest

from automate_jev.activity import (
    LocalActivityStore,
    ObservedActivity,
    PassiveActivityMonitor,
    PassiveAutomationService,
    PassiveRoutineLearner,
    WindowsForegroundActivitySource,
)
from automate_jev.models import Action, Risk
from automate_jev.orchestrator import RunResult, RunStatus
from automate_jev.routine import AutonomousRoutineRunner


def make_activity(start, index):
    return ObservedActivity(
        observed_at=start + timedelta(days=index),
        surface="slack",
        topic="daily team summary",
        foreground="Slack / team channel",
    )


def make_action():
    return Action(
        id="slack.daily-summary",
        domain="mcp.slack",
        verb="send",
        description="Send a daily team summary to Slack.",
        risk=Risk.CONFIRM,
    )


def test_passive_learner_infers_action_from_observed_context():
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    activities = [make_activity(start, index) for index in range(5)]

    pattern = PassiveRoutineLearner().learn(activities, [make_action()])[0]

    assert pattern.action_id == "slack.daily-summary"
    assert pattern.context == (("surface", "slack"), ("topic", "daily team summary"), ("hour", "9"))
    assert pattern.auto_execute is False


def test_store_round_trips_observations_without_action_id(tmp_path):
    store = LocalActivityStore(tmp_path / "activity.jsonl")
    activity = make_activity(datetime(2026, 1, 1, 9, tzinfo=timezone.utc), 0)

    store.append(activity)

    loaded = store.read()
    assert loaded == (activity,)
    assert "action_id" not in (tmp_path / "activity.jsonl").read_text()


def test_windows_source_is_explicitly_platform_bound():
    if __import__("os").name == "nt":
        return
    with pytest.raises(Exception, match="requires Windows"):
        import asyncio

        asyncio.run(WindowsForegroundActivitySource().current_activity())


@pytest.mark.asyncio
async def test_monitor_deduplicates_unchanged_foreground_activity(tmp_path):
    activity = make_activity(datetime(2026, 1, 1, 9, tzinfo=timezone.utc), 0)

    class Source:
        async def current_activity(self):
            return activity

    monitor = PassiveActivityMonitor(Source(), LocalActivityStore(tmp_path / "activity.jsonl"))

    assert await monitor.sample() == activity
    assert await monitor.sample() is None


@pytest.mark.asyncio
async def test_service_runs_the_full_passive_tick(tmp_path):
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    activities = [make_activity(start, index) for index in range(5)]

    class Source:
        def __init__(self):
            self.index = 0

        async def current_activity(self):
            activity = activities[self.index]
            self.index = min(self.index + 1, len(activities) - 1)
            return activity

    calls = []

    async def execute(pattern, key):
        calls.append(key)
        return RunResult(RunStatus.SUCCESS, action_id=pattern.action_id)

    monitor = PassiveActivityMonitor(Source(), LocalActivityStore(tmp_path / "activity.jsonl"))
    service = PassiveAutomationService(
        monitor=monitor,
        learner=PassiveRoutineLearner(),
        actions=(make_action(),),
        runner=AutonomousRoutineRunner(execute),
    )
    for index in range(5):
        await service.tick(now=start + timedelta(days=index))

    assert calls == []