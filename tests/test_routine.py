from datetime import datetime, timedelta, timezone

import pytest

from automate_jev.models import Action, Risk
from automate_jev.routine import AutonomousRoutineRunner, RoutineCadence, RoutineLearner, UsageEvent
from automate_jev.orchestrator import RunResult, RunStatus


def make_action(*, risk=Risk.SAFE):
    return Action(
        id="slack.daily-summary",
        domain="mcp.slack",
        verb="send",
        description="Send the daily summary.",
        expected_effects=({"type": "fact_equals", "key": "sent", "value": True, "risk_effect": "fill_demo_text"},),
        risk=risk,
    )


def events(start, *, count=4, step=timedelta(days=1), succeeded=True):
    return [UsageEvent("slack.daily-summary", start + index * step, succeeded=succeeded) for index in range(count)]


def test_learner_detects_safe_daily_routine():
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)

    pattern = RoutineLearner().learn(events(start), [make_action()])[0]

    assert pattern.cadence is RoutineCadence.DAILY
    assert pattern.auto_execute is True
    assert pattern.next_due_at == start + timedelta(days=4)


def test_learner_keeps_unreliable_or_confirm_routines_manual():
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    pattern = RoutineLearner().learn(events(start, succeeded=False), [make_action(risk=Risk.CONFIRM)])[0]

    assert pattern.auto_execute is False
    assert pattern.success_rate == 0


@pytest.mark.asyncio
async def test_runner_executes_only_due_autonomous_patterns():
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    pattern = RoutineLearner().learn(events(start), [make_action()])[0]
    called = []

    async def execute(candidate, idempotency_key):
        called.append((candidate.routine_id, idempotency_key))
        return RunResult(RunStatus.SUCCESS, action_id=candidate.action_id)

    results = await AutonomousRoutineRunner(execute).run_due(
        [pattern], now=start + timedelta(days=4, seconds=1)
    )

    assert results[0].status is RunStatus.SUCCESS
    assert called[0][1].startswith("routine:slack.daily-summary:")