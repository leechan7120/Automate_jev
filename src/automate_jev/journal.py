from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from .models import ActionEnvelope, ContractError, canonical_json


class JournalCorruption(RuntimeError):
    pass


class JournalState(StrEnum):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    OBSERVED = "OBSERVED"
    COMMITTED = "COMMITTED"
    UNKNOWN = "UNKNOWN"


ALLOWED_TRANSITIONS = {
    None: {JournalState.PREPARED},
    JournalState.PREPARED: {JournalState.EXECUTING},
    JournalState.EXECUTING: {JournalState.OBSERVED, JournalState.UNKNOWN},
    JournalState.OBSERVED: {JournalState.COMMITTED, JournalState.UNKNOWN},
    JournalState.COMMITTED: set(),
    JournalState.UNKNOWN: {JournalState.COMMITTED},
}


@dataclass(frozen=True, slots=True)
class JournalEntry:
    sequence: int
    idempotency_key: str
    action_hash: str
    state: JournalState
    detail: dict[str, Any]
    previous_checksum: str
    checksum: str


class ExecutionJournal:
    """Append-only checksum chain with an atomically replaced state index."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_path = self.directory / "journal.jsonl"
        self.index_path = self.directory / "journal-index.json"
        self._lock = RLock()
        self._entries = self._load()
        self._states = {entry.idempotency_key: entry.state for entry in self._entries}
        self._action_hashes = {entry.idempotency_key: entry.action_hash for entry in self._entries}
        self._write_index()

    def state(self, idempotency_key: str) -> JournalState | None:
        return self._states.get(idempotency_key)

    def prepare(self, envelope: ActionEnvelope) -> JournalEntry:
        return self.transition(
            envelope.idempotency_key,
            envelope.action_hash,
            JournalState.PREPARED,
            {"session_id": envelope.session_id, "registry_version": envelope.registry_version},
        )

    def transition(
        self,
        idempotency_key: str,
        action_hash: str,
        state: JournalState,
        detail: dict[str, Any] | None = None,
    ) -> JournalEntry:
        with self._lock:
            current = self._states.get(idempotency_key)
            if state not in ALLOWED_TRANSITIONS[current]:
                raise ContractError(f"invalid journal transition {current!s} -> {state}")
            previous_action_hash = self._action_hashes.get(idempotency_key)
            if previous_action_hash is not None and previous_action_hash != action_hash:
                raise ContractError("idempotency key is bound to a different action hash")
            previous_checksum = self._entries[-1].checksum if self._entries else "GENESIS"
            body = {
                "sequence": len(self._entries) + 1,
                "idempotency_key": idempotency_key,
                "action_hash": action_hash,
                "state": state.value,
                "detail": detail or {},
                "previous_checksum": previous_checksum,
            }
            checksum = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
            record = {**body, "checksum": checksum}
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(canonical_json(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            entry = JournalEntry(
                sequence=body["sequence"],
                idempotency_key=idempotency_key,
                action_hash=action_hash,
                state=state,
                detail=dict(body["detail"]),
                previous_checksum=previous_checksum,
                checksum=checksum,
            )
            self._entries.append(entry)
            self._states[idempotency_key] = state
            self._action_hashes[idempotency_key] = action_hash
            self._write_index()
            return entry

    def incomplete(self) -> dict[str, JournalState]:
        return {
            key: state
            for key, state in self._states.items()
            if state not in {JournalState.COMMITTED}
        }

    def _load(self) -> list[JournalEntry]:
        if not self.log_path.exists():
            return []
        entries: list[JournalEntry] = []
        previous = "GENESIS"
        for line_number, line in enumerate(self.log_path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
                checksum = record.pop("checksum")
                actual = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
                if checksum != actual or record["previous_checksum"] != previous:
                    raise JournalCorruption(f"journal checksum mismatch at line {line_number}")
                entry = JournalEntry(
                    sequence=int(record["sequence"]),
                    idempotency_key=str(record["idempotency_key"]),
                    action_hash=str(record["action_hash"]),
                    state=JournalState(record["state"]),
                    detail=dict(record["detail"]),
                    previous_checksum=str(record["previous_checksum"]),
                    checksum=str(checksum),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise JournalCorruption(f"invalid journal record at line {line_number}") from error
            entries.append(entry)
            previous = entry.checksum
        return entries

    def _write_index(self) -> None:
        payload = {
            "last_sequence": len(self._entries),
            "states": {key: value.value for key, value in sorted(self._states.items())},
        }
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=self.directory,
            prefix="journal-index-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(canonical_json(payload) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.index_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

