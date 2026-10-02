from dataclasses import replace

import pytest

from automate_jev.journal import ExecutionJournal, JournalState
from automate_jev.models import Action, Risk
from automate_jev.orchestrator import AgentOrchestrator, RunStatus
from automate_jev.policy import ApprovalService, PolicyGate
from automate_jev.registry import ActionRegistry
from automate_jev.simulation import FixedDecisionProvider, SimulatedRuntime


def action(*, risk=Risk.SAFE, effect="fill_demo_text"):
    return Action(
        id="demo.fill",
        domain="desktop",
        verb="fill",
        description="Fill approved demo text.",
        arguments={"text": "DEMO_APPROVED"},
        expected_effects=({
            "type": "fact_equals",
            "key": "filled",
            "value": True,
            "risk_effect": effect,
        },),
        risk=risk,
    )


def build(tmp_path, selected="demo.fill", confidence=1.0, *, chosen_action=None):
    chosen_action = chosen_action or action()
    registry = ActionRegistry("demo-session")
    registry.replace([chosen_action])
    runtime = SimulatedRuntime({"filled": False})
    approvals = ApprovalService(b"test-secret")
    orchestrator = AgentOrchestrator(
        registry=registry,
        provider=FixedDecisionProvider(selected, confidence),
        runtime=runtime,
        policy=PolicyGate(approvals),
        journal=ExecutionJournal(tmp_path),
    )
    return orchestrator, runtime, approvals


@pytest.mark.asyncio
async def test_happy_path_commits_once(tmp_path):
    orchestrator, runtime, _ = build(tmp_path)
    result = await orchestrator.run_next(goal="fill", idempotency_key="run-1:step-1")
    assert result.status is RunStatus.SUCCESS
    assert runtime.executions == 1
    assert orchestrator.journal.state("run-1:step-1") is JournalState.COMMITTED
    duplicate = await orchestrator.run_next(goal="fill", idempotency_key="run-1:step-1")
    assert duplicate.status is RunStatus.BLOCKED
    assert runtime.executions == 1


@pytest.mark.asyncio
async def test_unknown_candidate_and_low_confidence_do_not_execute(tmp_path):
    orchestrator, runtime, _ = build(tmp_path, selected="invented.action")
    invalid = await orchestrator.run_next(goal="fill", idempotency_key="run-1:step-1")
    assert invalid.status is RunStatus.INVALID_DECISION
    assert runtime.executions == 0

    other, other_runtime, _ = build(tmp_path / "low", confidence=0.2)
    low = await other.run_next(goal="fill", idempotency_key="run-2:step-1")
    assert low.status is RunStatus.ABSTAINED
    assert other_runtime.executions == 0


@pytest.mark.asyncio
async def test_confirm_action_never_executes_without_approval(tmp_path):
    confirm = action(risk=Risk.CONFIRM, effect="upload")
    orchestrator, runtime, _ = build(tmp_path, chosen_action=confirm)
    result = await orchestrator.run_next(goal="upload", idempotency_key="run-1:step-1")
    assert result.status is RunStatus.NEEDS_APPROVAL
    assert runtime.executions == 0


@pytest.mark.asyncio
async def test_confirm_action_executes_with_matching_approval(tmp_path):
    confirm = action(risk=Risk.CONFIRM, effect="upload")
    orchestrator, runtime, approvals = build(tmp_path, chosen_action=confirm)
    observation = await runtime.observe()
    envelope = orchestrator.registry.envelope(
        confirm.id,
        observation,
        "approval-preview",
    )
    token = approvals.issue(envelope)
    result = await orchestrator.run_next(
        goal="upload",
        idempotency_key="run-1:step-1",
        approval=token,
    )
    assert result.status is RunStatus.SUCCESS
    assert runtime.executions == 1


@pytest.mark.asyncio
async def test_stale_revision_fails_closed(tmp_path):
    orchestrator, runtime, _ = build(tmp_path)

    class MutatingProvider(FixedDecisionProvider):
        async def choose(self, **kwargs):
            runtime.facts["external_change"] = True
            return await super().choose(**kwargs)

    orchestrator.provider = MutatingProvider("demo.fill")
    result = await orchestrator.run_next(goal="fill", idempotency_key="run-1:step-1")
    assert result.status is RunStatus.STALE_ACTION
    assert runtime.executions == 0
    assert orchestrator.journal.state("run-1:step-1") is JournalState.UNKNOWN

