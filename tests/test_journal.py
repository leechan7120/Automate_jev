import json

import pytest

from automate_jev.journal import ExecutionJournal, JournalCorruption, JournalState
from automate_jev.models import Action, ContractError, Observation, Risk
from automate_jev.registry import ActionRegistry


def envelope():
    action = Action(
        id="demo.wait",
        domain="system",
        verb="wait",
        description="Wait for the demo state.",
        expected_effects=({"type": "fact_equals", "key": "ready", "value": True, "risk_effect": "wait"},),
        risk=Risk.SAFE,
        source="system",
    )
    registry = ActionRegistry("demo-session")
    registry.replace([action])
    return registry.envelope(action.id, Observation.capture("desktop", {"ready": False}), "run-1:step-1")


def test_journal_persists_valid_transition_chain(tmp_path):
    item = envelope()
    journal = ExecutionJournal(tmp_path)
    journal.prepare(item)
    journal.transition(item.idempotency_key, item.action_hash, JournalState.EXECUTING)
    journal.transition(item.idempotency_key, item.action_hash, JournalState.OBSERVED)
    journal.transition(item.idempotency_key, item.action_hash, JournalState.COMMITTED)
    reopened = ExecutionJournal(tmp_path)
    assert reopened.state(item.idempotency_key) is JournalState.COMMITTED
    assert reopened.incomplete() == {}


def test_duplicate_prepare_is_rejected(tmp_path):
    item = envelope()
    journal = ExecutionJournal(tmp_path)
    journal.prepare(item)
    with pytest.raises(ContractError, match="invalid journal transition"):
        journal.prepare(item)


def test_idempotency_key_cannot_change_action_hash(tmp_path):
    item = envelope()
    journal = ExecutionJournal(tmp_path)
    journal.prepare(item)
    with pytest.raises(ContractError, match="different action hash"):
        journal.transition(item.idempotency_key, "sha256:different", JournalState.EXECUTING)


def test_corruption_blocks_startup(tmp_path):
    item = envelope()
    journal = ExecutionJournal(tmp_path)
    journal.prepare(item)
    lines = journal.log_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["action_hash"] = "sha256:tampered"
    journal.log_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(JournalCorruption, match="checksum"):
        ExecutionJournal(tmp_path)
