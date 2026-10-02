from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

from .journal import ExecutionJournal
from .models import Action, Risk
from .orchestrator import AgentOrchestrator
from .policy import ApprovalService, PolicyGate
from .registry import ActionRegistry
from .simulation import FixedDecisionProvider, SimulatedRuntime


async def run_demo() -> None:
    action = Action(
        id="notepad.fill-required-text",
        domain="desktop",
        verb="fill",
        description="Fill the approved synthetic demo text in Notepad.",
        target={"process": "notepad.exe", "control_type": "Document"},
        arguments={"text": "DEMO_APPROVED"},
        preconditions=({"type": "fact_equals", "key": "required_text_present", "value": False},),
        expected_effects=({
            "type": "fact_equals",
            "key": "required_text_present",
            "value": True,
            "risk_effect": "fill_demo_text",
        },),
        risk=Risk.SAFE,
    )
    registry = ActionRegistry("demo-session")
    registry.replace([action])
    runtime = SimulatedRuntime({"required_text_present": False})
    approvals = ApprovalService(b"demo-only-secret-not-for-production")
    with tempfile.TemporaryDirectory(prefix="automate-jev-demo-") as directory:
        orchestrator = AgentOrchestrator(
            registry=registry,
            provider=FixedDecisionProvider(action.id),
            runtime=runtime,
            policy=PolicyGate(approvals),
            journal=ExecutionJournal(Path(directory)),
        )
        result = await orchestrator.run_next(
            goal="Add the approved marker to the sample file.",
            idempotency_key="demo-run:step-1",
        )
        print({"status": result.status.value, "action_id": result.action_id, "facts": runtime.facts})


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()

