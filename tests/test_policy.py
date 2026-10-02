from dataclasses import replace

import pytest

from automate_jev.models import Action, ContractError, Observation, Risk
from automate_jev.policy import ApprovalService, PolicyGate, required_risk
from automate_jev.registry import ActionRegistry


def action_for(effect: str, risk: Risk) -> Action:
    return Action(
        id=f"demo.{effect}",
        domain="browser",
        verb="invoke",
        description=f"Perform {effect}.",
        expected_effects=({
            "type": "fact_equals",
            "key": "done",
            "value": True,
            "risk_effect": effect,
        },),
        risk=risk,
    )


def envelope_for(action: Action):
    registry = ActionRegistry("demo-session")
    registry.replace([action])
    observation = Observation.capture("browser", {"done": False})
    return registry.envelope(action.id, observation, "run-1:step-1")


def test_effect_based_risk_classification():
    assert required_risk(action_for("wait", Risk.SAFE)) is Risk.SAFE
    assert required_risk(action_for("upload", Risk.CONFIRM)) is Risk.CONFIRM
    assert required_risk(action_for("delete", Risk.BLOCKED)) is Risk.BLOCKED
    assert required_risk(action_for("unknown_effect", Risk.CONFIRM)) is Risk.CONFIRM


def test_confirm_action_requires_bound_approval():
    approvals = ApprovalService(b"test-secret")
    gate = PolicyGate(approvals)
    envelope = envelope_for(action_for("upload", Risk.CONFIRM))
    with pytest.raises(ContractError, match="approval required"):
        gate.authorize(envelope, None)
    token = approvals.issue(envelope)
    gate.authorize(envelope, token)


def test_approval_does_not_authorize_changed_arguments():
    approvals = ApprovalService(b"test-secret")
    original = envelope_for(action_for("upload", Risk.CONFIRM))
    token = approvals.issue(original)
    changed_action = replace(original.action, arguments={"path": "other.txt"})
    changed = replace(original, action=changed_action)
    with pytest.raises(ContractError, match="action_hash mismatch"):
        approvals.verify(token, changed)


def test_declared_safe_cannot_downgrade_upload():
    envelope = envelope_for(action_for("upload", Risk.SAFE))
    with pytest.raises(ContractError, match="does not match"):
        PolicyGate(ApprovalService()).authorize(envelope, None)

