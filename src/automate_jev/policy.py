from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any

from .models import Action, ActionEnvelope, ContractError, Risk, canonical_json


BLOCKED_EFFECTS = {"delete", "payment", "electronic_signature", "permission_change", "credential_input"}
CONFIRM_EFFECTS = {"upload", "submit", "send", "overwrite", "save_existing", "move"}
SAFE_EFFECTS = {"read", "focus", "switch_window", "wait", "fill_demo_text", "save_new_demo_file"}


def required_risk(action: Action) -> Risk:
    effect_types = {
        str(effect.get("risk_effect") or effect.get("type", ""))
        for effect in action.expected_effects
    }
    if effect_types & BLOCKED_EFFECTS:
        return Risk.BLOCKED
    if effect_types & CONFIRM_EFFECTS:
        return Risk.CONFIRM
    if not effect_types or not effect_types <= SAFE_EFFECTS:
        return Risk.CONFIRM
    return Risk.SAFE


@dataclass(frozen=True, slots=True)
class ApprovalToken:
    payload: dict[str, Any]
    signature: str


class ApprovalService:
    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret or secrets.token_bytes(32)

    def issue(self, envelope: ActionEnvelope, *, ttl_seconds: int = 60) -> ApprovalToken:
        if envelope.action.risk is not Risk.CONFIRM:
            raise ContractError("approval tokens are only valid for confirm actions")
        expiry = min(
            envelope.expiry(),
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        )
        payload = {
            "session_id": envelope.session_id,
            "action_hash": envelope.action_hash,
            "state_revision": envelope.expected_state_revision,
            "arguments_hash": envelope.arguments_hash,
            "risk": envelope.action.risk.value,
            "expires_at": expiry.isoformat(),
        }
        return ApprovalToken(payload=payload, signature=self._sign(payload))

    def verify(self, token: ApprovalToken, envelope: ActionEnvelope) -> None:
        expected = {
            "session_id": envelope.session_id,
            "action_hash": envelope.action_hash,
            "state_revision": envelope.expected_state_revision,
            "arguments_hash": envelope.arguments_hash,
            "risk": envelope.action.risk.value,
        }
        if not hmac.compare_digest(token.signature, self._sign(token.payload)):
            raise ContractError("approval signature mismatch")
        for key, value in expected.items():
            if token.payload.get(key) != value:
                raise ContractError(f"approval {key} mismatch")
        try:
            expiry = datetime.fromisoformat(str(token.payload["expires_at"]))
        except (KeyError, ValueError) as error:
            raise ContractError("approval expiry is invalid") from error
        if expiry.tzinfo is None or datetime.now(timezone.utc) >= expiry:
            raise ContractError("approval expired")

    def _sign(self, payload: dict[str, Any]) -> str:
        return hmac.new(
            self._secret,
            canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


class PolicyGate:
    def __init__(self, approvals: ApprovalService) -> None:
        self._approvals = approvals

    def authorize(self, envelope: ActionEnvelope, approval: ApprovalToken | None) -> None:
        classified = required_risk(envelope.action)
        if classified is not envelope.action.risk:
            raise ContractError(
                f"declared risk {envelope.action.risk.value} does not match required {classified.value}"
            )
        if classified is Risk.BLOCKED:
            raise ContractError("blocked action cannot execute")
        if classified is Risk.CONFIRM:
            if approval is None:
                raise ContractError("approval required")
            self._approvals.verify(approval, envelope)

