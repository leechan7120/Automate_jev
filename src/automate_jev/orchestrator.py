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
    confidence: float | None = None
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
        minimum_safe_confidence: float = 0.5,
    ) -> None:
        if not 0 <= minimum_safe_confidence <= minimum_confidence <= 1:
            raise ContractError(
                "confidence thresholds must satisfy 0 <= safe <= default <= 1"
            )
        self.registry = registry
        self.provider = provider
        self.runtime = runtime
        self.policy = policy
        self.journal = journal
        self.minimum_confidence = minimum_confidence
        self.minimum_safe_confidence = minimum_safe_confidence

    async def run_next(
        self,
        *,
        goal: str,
        idempotency_key: str,
        approval: ApprovalToken | None = None,
    ) -> RunResult:
        if self.journal.state(idempotency_key) is not None:
            return RunResult(RunStatus.BLOCKED, detail="idempotency key already exists")
        ensure_registered = getattr(self.runtime, "ensure_registered", None)
        if ensure_registered is not None:
            try:
                await ensure_registered(self.registry.snapshot())
            except Exception as error:
                return RunResult(
                    RunStatus.BLOCKED,
                    detail=f"registry synchronization failed: {type(error).__name__}",
                )
        observation = await self.runtime.observe()
        try:
            decision = await self.provider.choose(
                goal=goal,
                observation=observation,
                candidates=self.registry.candidate_descriptions(),
            )
        except Exception as error:
            return RunResult(RunStatus.ABSTAINED, detail=f"provider error: {type(error).__name__}")
        if decision.action_id == "abstain":
            return RunResult(
                RunStatus.ABSTAINED,
                action_id=decision.action_id,
                confidence=decision.confidence,
                detail="provider selected abstain",
            )
        try:
            envelope = self.registry.envelope(decision.action_id, observation, idempotency_key)
            self.registry.validate(envelope)
        except ContractError as error:
            return RunResult(RunStatus.INVALID_DECISION, decision.action_id, decision.confidence, str(error))
        minimum_confidence = (
            self.minimum_safe_confidence
            if envelope.action.risk is Risk.SAFE
            else self.minimum_confidence
        )
        if decision.confidence < minimum_confidence:
            return RunResult(
                RunStatus.ABSTAINED,
                decision.action_id,
                decision.confidence,
                f"confidence below {minimum_confidence:.2f} for {envelope.action.risk.value} action",
            )
        if envelope.action.risk is Risk.CONFIRM and approval is None:
            return RunResult(RunStatus.NEEDS_APPROVAL, decision.action_id, decision.confidence)
        try:
            self.policy.authorize(envelope, approval)
        except ContractError as error:
            return RunResult(RunStatus.BLOCKED, decision.action_id, decision.confidence, str(error))

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
            return RunResult(status, decision.action_id, decision.confidence, str(error))
        except Exception as error:
            self.journal.transition(
                idempotency_key,
                envelope.action_hash,
                JournalState.UNKNOWN,
                {"error": type(error).__name__},
            )
            return RunResult(RunStatus.UNKNOWN, decision.action_id, decision.confidence)

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
            return RunResult(RunStatus.UNKNOWN, decision.action_id, decision.confidence)
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
        return RunResult(RunStatus.SUCCESS, decision.action_id, decision.confidence)
