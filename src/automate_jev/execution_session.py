from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .draft_review import DraftReviewService, DraftReviewToken
from .models import Action, ContractError
from .workflow import WorkflowDefinition


class ExecutionSessionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    ABSTAINED = "ABSTAINED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ExecutionSession:
    session_id: str
    workflow_id: str
    workflow_hash: str
    provider: str
    status: ExecutionSessionStatus
    created_at: str
    updated_at: str
    timeline: list[dict[str, str]] = field(default_factory=list)
    result: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workflow_id": self.workflow_id,
            "workflow_hash": self.workflow_hash,
            "provider": self.provider,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "timeline": [dict(item) for item in self.timeline],
            "result": dict(self.result) if self.result is not None else None,
        }


ExecutionRunner = Callable[[WorkflowDefinition, str, str], Awaitable[dict[str, Any]]]


class ExecutionSessionService:
    """Exchanges an exact review token for one bounded execution session."""

    def __init__(
        self,
        *,
        reviews: DraftReviewService,
        trusted_actions: tuple[Action, ...],
        runner: ExecutionRunner,
    ) -> None:
        self._reviews = reviews
        self._trusted_actions = trusted_actions
        self._runner = runner
        self._sessions: dict[str, ExecutionSession] = {}
        self._consumed_reviews: set[str] = set()
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        workflow: WorkflowDefinition,
        review: DraftReviewToken,
        provider: str,
    ) -> ExecutionSession:
        if provider not in {"fixture", "live"}:
            raise ContractError("provider must be fixture or live")
        workflow.bind_actions(self._trusted_actions)
        self._reviews.verify(review, workflow)
        if len(workflow.steps) != 1:
            raise ContractError("this MVP execution path requires exactly one workflow step")

        async with self._lock:
            if review.signature in self._consumed_reviews:
                raise ContractError("review token has already been exchanged")
            self._consumed_reviews.add(review.signature)
            now = datetime.now(timezone.utc).isoformat()
            session = ExecutionSession(
                session_id=f"web-{uuid4().hex}",
                workflow_id=workflow.workflow_id,
                workflow_hash=str(review.payload["workflow_hash"]),
                provider=provider.upper(),
                status=ExecutionSessionStatus.QUEUED,
                created_at=now,
                updated_at=now,
                timeline=[],
            )
            self._append(session, "REVIEW_VERIFIED", "검토 토큰과 Workflow hash가 일치합니다.")
            self._append(session, "SESSION_CREATED", "일회성 실행 세션을 생성했습니다.")
            self._sessions[session.session_id] = session
            return session

    async def run(self, session_id: str, workflow: WorkflowDefinition) -> None:
        session = self._require(session_id)
        session.status = ExecutionSessionStatus.RUNNING
        self._append(session, "EXECUTING", "Jev 판단과 로컬 안전 실행을 시작했습니다.")
        try:
            result = await self._runner(workflow, session.provider.lower(), session.session_id)
            session.result = dict(result)
            raw_status = str(result.get("status", "UNKNOWN"))
            session.status = self._map_status(raw_status)
            self._append(
                session,
                "FINISHED",
                f"실행 결과: {session.status.value}",
            )
        except Exception as error:
            session.status = ExecutionSessionStatus.FAILED
            session.result = {"status": "FAILED", "detail": type(error).__name__}
            self._append(session, "FAILED", f"실행을 완료하지 못했습니다: {type(error).__name__}")

    def get(self, session_id: str) -> ExecutionSession:
        return self._require(session_id)

    def _require(self, session_id: str) -> ExecutionSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ContractError("execution session was not found")
        return session

    @staticmethod
    def _append(session: ExecutionSession, stage: str, detail: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        session.updated_at = now
        session.timeline.append({"at": now, "stage": stage, "detail": detail})

    @staticmethod
    def _map_status(status: str) -> ExecutionSessionStatus:
        if status == "SUCCESS":
            return ExecutionSessionStatus.SUCCESS
        if status == "ABSTAINED":
            return ExecutionSessionStatus.ABSTAINED
        if status in {"BLOCKED", "NEEDS_APPROVAL", "STALE_ACTION", "INVALID_DECISION"}:
            return ExecutionSessionStatus.BLOCKED
        if status == "UNKNOWN":
            return ExecutionSessionStatus.UNKNOWN
        return ExecutionSessionStatus.FAILED
