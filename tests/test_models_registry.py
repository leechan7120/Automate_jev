from datetime import datetime, timedelta, timezone

import pytest

from automate_jev.models import Action, ContractError, Observation, Risk
from automate_jev.registry import ActionRegistry


def make_action(**changes):
    values = {
        "id": "demo.fill",
        "domain": "desktop",
        "verb": "fill",
        "description": "Fill approved demo text.",
        "target": {"process": "notepad.exe"},
        "arguments": {"text": "DEMO_APPROVED"},
        "expected_effects": ({
            "type": "fact_equals",
            "key": "filled",
            "value": True,
            "risk_effect": "fill_demo_text",
        },),
        "risk": Risk.SAFE,
    }
    values.update(changes)
    return Action(**values)


def test_action_hash_is_stable_and_covers_arguments():
    first = make_action(arguments={"text": "DEMO_APPROVED", "line": 1})
    reordered = make_action(arguments={"line": 1, "text": "DEMO_APPROVED"})
    changed = make_action(arguments={"text": "DIFFERENT", "line": 1})
    assert first.action_hash == reordered.action_hash
    assert first.action_hash != changed.action_hash


def test_observation_separates_schema_and_state_revision():
    first = Observation.capture("desktop", {"filled": False})
    same = Observation.capture("desktop", {"filled": False})
    changed = Observation.capture("desktop", {"filled": True})
    assert first.schema_version == "1.0"
    assert first.state_revision == same.state_revision
    assert first.state_revision != changed.state_revision


def test_registry_rejects_stale_version_and_tampered_action():
    registry = ActionRegistry("demo-session")
    action = make_action()
    registry.replace([action])
    observation = Observation.capture("desktop", {"filled": False})
    envelope = registry.envelope(action.id, observation, "run-1:step-1")
    registry.replace([make_action(description="New registry revision")])
    with pytest.raises(ContractError, match="registry version"):
        registry.validate(envelope)


def test_registry_limits_candidate_count():
    registry = ActionRegistry("demo-session")
    actions = [make_action(id=f"demo.fill-{index}") for index in range(13)]
    with pytest.raises(ContractError, match="1 to 12"):
        registry.replace(actions)

