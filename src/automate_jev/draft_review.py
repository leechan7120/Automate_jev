from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
from typing import Any

from .models import ContractError, canonical_json, digest_json
from .workflow import WorkflowDefinition


@dataclass(frozen=True, slots=True)
class DraftReviewToken:
    payload: dict[str, Any]
    signature: str


class DraftReviewService:
    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)

    def issue(self, workflow: WorkflowDefinition, *, ttl_seconds: int = 900) -> DraftReviewToken:
        if not 60 <= ttl_seconds <= 3600:
            raise ContractError("review token TTL must be between 60 and 3600 seconds")
        payload = {
            "workflow_id": workflow.workflow_id,
            "workflow_hash": digest_json(workflow.payload()),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            "purpose": "workflow_draft_review",
        }
        return DraftReviewToken(payload=payload, signature=self._sign(payload))

    def verify(self, token: DraftReviewToken, workflow: WorkflowDefinition) -> None:
        if not hmac.compare_digest(token.signature, self._sign(token.payload)):
            raise ContractError("review signature mismatch")
        if token.payload.get("purpose") != "workflow_draft_review":
            raise ContractError("review token purpose mismatch")
        if token.payload.get("workflow_id") != workflow.workflow_id:
            raise ContractError("review workflow mismatch")
        if token.payload.get("workflow_hash") != digest_json(workflow.payload()):
            raise ContractError("review workflow hash mismatch")
        try:
            expiry = datetime.fromisoformat(str(token.payload["expires_at"]))
        except (KeyError, ValueError) as error:
            raise ContractError("review expiry is invalid") from error
        if expiry.tzinfo is None or datetime.now(timezone.utc) >= expiry:
            raise ContractError("review token expired")

    def _sign(self, payload: dict[str, Any]) -> str:
        return hmac.new(
            self._secret,
            canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
