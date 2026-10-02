import json
from pathlib import Path

import pytest

from automate_jev.models import Action, ContractError, Risk
from automate_jev.workflow import MAX_WORKFLOW_BYTES, load_workflow


ROOT = Path(__file__).parents[1]


def registered_action(action_id: str, risk: Risk, *, domain: str | None = None) -> Action:
    effect = "upload" if risk is Risk.CONFIRM else "read"
    return Action(
        id=action_id,
        domain=domain or ("browser" if action_id.startswith("portal.") else "desktop"),
        verb="invoke",
        description=f"Trusted action {action_id}",
        expected_effects=(
            {"type": "fact_equals", "key": action_id, "value": True, "risk_effect": effect},
        ),
        risk=risk,
    )


def test_example_workflow_loads_and_binds_only_registered_actions():
    workflow = load_workflow(ROOT / "examples" / "local-portal-notepad-upload.json")
    actions = (
        registered_action("portal.download-sample", Risk.SAFE),
        registered_action("notepad.fill-required-text", Risk.SAFE),
        registered_action("portal.upload-sample", Risk.CONFIRM),
    )

    bound = workflow.bind_actions(actions)

    assert workflow.workflow_id == "local-portal-notepad-upload"
    assert [action.id for action in bound] == [step.action_id for step in workflow.steps]
    assert workflow.next_step({}).id == "download-sample"
    assert workflow.is_complete({}) is False
    assert workflow.is_complete(
        {"browser_text_visible": "등록되었습니다", "browser_url_matches": "*/complete"}
    ) is True


def test_workflow_rejects_unknown_action_and_risk_mismatch():
    workflow = load_workflow(ROOT / "examples" / "local-portal-notepad-upload.json")
    with pytest.raises(ContractError, match="not registered"):
        workflow.bind_actions(())
    with pytest.raises(ContractError, match="risk does not match"):
        workflow.bind_actions(
            (
                registered_action("portal.download-sample", Risk.CONFIRM),
                registered_action("notepad.fill-required-text", Risk.SAFE),
                registered_action("portal.upload-sample", Risk.CONFIRM),
            )
        )


def test_workflow_rejects_duplicate_keys_and_unknown_properties(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
    with pytest.raises(ContractError, match="valid UTF-8 JSON"):
        load_workflow(duplicate)

    document = json.loads((ROOT / "examples" / "local-portal-notepad-upload.json").read_text("utf-8"))
    document["unexpected"] = True
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="schema validation failed"):
        load_workflow(invalid)


def test_workflow_rejects_relative_root_and_oversized_input(tmp_path):
    document = json.loads((ROOT / "examples" / "local-portal-notepad-upload.json").read_text("utf-8"))
    document["allowed_root"] = "relative/path"
    relative = tmp_path / "relative.json"
    relative.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractError, match="absolute path"):
        load_workflow(relative)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_WORKFLOW_BYTES + 1))
    with pytest.raises(ContractError, match="must contain"):
        load_workflow(oversized)
