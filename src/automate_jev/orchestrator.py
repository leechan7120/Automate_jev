from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .journal import ExecutionJournal, JournalState
from .models import ActionEnvelope, ContractError, Decision, Observation, Risk
from .policy import ApprovalToken, PolicyGate
from .registry import ActionRegistry
from .verifier import VerificationStatus, verify_until_stable


class DecisionProvider(Protocol):
    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: dict[str, str],
    ) -> Decision: ...


class RuntimeAdapter(Protocol):
    async def observe(self) -> Observation: ...

    async def execute_if_current(self, envelope: ActionEnvelope) -> None: ...


class RunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ABSTAINED = "ABSTAINED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"
    STALE_ACTION = "STALE_ACTION"
    INVALID_DECISION = "INVALID_DECISION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    action_id: str | None = None
    detail: str | None = None


class AgentOrchestrator:
    def __init__(
        self,
        *,
        registry: ActionRegistry,
        provider: DecisionProvider,
        runtime: RuntimeAdapter,
        policy: PolicyGate,
        journal: ExecutionJournal,
        minimum_confidence: float = 0.8,
    ) -> None:
        self.registry = registry
        self.provider = provider
        self.runtime = runtime
        self.policy = policy
        self.journal = journal
        self.minimum_confidence = minimum_confidence

    async def run_next(
        self,
        *,
        goal: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> RunResult:
        if self.journal.state(idempotency_key) is not None:
            return RunResult(RunStatus.BLOCKED, detail="idempotency key already exists")
        observation = await self.runtime.observe()
        try:
            decision = await self.provider.choose(
                goal=goal,
                observation=observation,
                candidates=self.registry.candidate_descriptions(),
            )
        except Exception as error:
            return RunResult(RunStatus.ABSTAINED, detail=f"provider error: {type(error).__name__}")
        if decision.action_id == "abstain" or decision.confidence < self.minimum_confidence:
            return RunResult(RunStatus.ABSTAINED, action_id=decision.action_id)
        try:
            envelope = self.registry.envelope(decision.action_id, observation, idempotency_key)
            self.registry.validate(envelope)
        except ContractError as error:
            return RunResult(RunStatus.INVALID_DECISION, action_id=decision.action_id, detail=str(error))
        if envelope.action.risk is Risk.CONFIRM and approval is None:
            return RunResult(RunStatus.NEEDS_APPROVAL, action_id=decision.action_id)
        try:
            self.policy.authorize(envelope, approval)
        except ContractError as error:
            return RunResult(RunStatus.BLOCKED, action_id=decision.action_id, detail=str(error))

        self.journal.prepare(envelope)
        self.journal.transition(
            idempotency_key,
            envelope.action_hash,
            JournalState.EXECUTING,
            {"expected_state_revision": envelope.expected_state_revision},
        )
        try:
            await self.runtime.execute_if_current(envelope)
        except ContractError as error:
            self.journal.transition(
                idempotency_key,
                envelope.action_hash,
                JournalState.UNKNOWN,
                {"error": str(error)},
            )
            status = RunStatus.STALE_ACTION if "revision" in str(error) or "target" in str(error) else RunStatus.UNKNOWN
            return RunResult(status, action_id=decision.action_id, detail=str(error))
        except Exception as error:
            self.journal.transition(
                idempotency_key,
                envelope.action_hash,
                JournalState.UNKNOWN,
                {"error": type(error).__name__},
            )
            return RunResult(RunStatus.UNKNOWN, action_id=decision.action_id)

        verification = await verify_until_stable(
            self.runtime.observe,
            envelope.action.expected_effects,
        )
        if verification is not VerificationStatus.SUCCESS:
            self.journal.transition(
                idempotency_key,
                envelope.action_hash,
                JournalState.UNKNOWN,
                {"verification": verification.value},
            )
            return RunResult(RunStatus.UNKNOWN, action_id=decision.action_id)
        self.journal.transition(
            idempotency_key,
            envelope.action_hash,
            JournalState.OBSERVED,
            {"verification": verification.value},
        )
        self.journal.transition(
            idempotency_key,
            envelope.action_hash,
            JournalState.COMMITTED,
        )
        return RunResult(RunStatus.SUCCESS, action_id=decision.action_id)
