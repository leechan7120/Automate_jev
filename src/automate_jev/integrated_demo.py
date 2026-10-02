from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid4

from .bridge import JsonLinesBridgeClient
from .bridge_runtime import BridgeRuntimeAdapter
from .env import load_jev_api_key
from .jev_provider import JevDecisionProvider
from .journal import ExecutionJournal
from .models import Action, ContractError, Risk
from .orchestrator import AgentOrchestrator
from .policy import ApprovalService, PolicyGate
from .registry import ActionRegistry
from .simulation import FixedDecisionProvider
from .windows_ui_smoke import wait_for_owned_foreground


def synthetic_fill_action() -> Action:
    return Action(
        id="smoke-target.fill-required-text",
        domain="desktop",
        verb="fill",
        description=(
            "Fill the approved DEMO_APPROVED marker only when the owned synthetic target "
            "is foreground and the marker is currently absent."
        ),
        target={"process": "AutomateJev.SmokeTarget.exe", "control_type": "Edit"},
        arguments={"text": "DEMO_APPROVED"},
        preconditions=(
            {"type": "fact_equals", "key": "required_text_present", "value": False},
        ),
        expected_effects=(
            {
                "type": "fact_equals",
                "key": "required_text_present",
                "value": True,
                "risk_effect": "fill_demo_text",
            },
        ),
        risk=Risk.SAFE,
    )


async def run_integrated_demo(
    *,
    host_path: Path,
    target_path: Path,
    journal_root: Path,
    provider_mode: str,
    env_path: Path,
    hold_seconds: float = 0,
) -> dict[str, Any]:
    if not host_path.is_file() or not target_path.is_file():
        raise ContractError("compiled host or smoke target is missing; build the Windows Host first")
    if provider_mode not in {"fixture", "live"}:
        raise ContractError("provider mode must be fixture or live")
    if not 0 <= hold_seconds <= 10:
        raise ContractError("hold_seconds must be between 0 and 10")

    session_id = f"integrated-{uuid4().hex}"
    action = synthetic_fill_action()
    registry = ActionRegistry(session_id)
    registry.replace([action])
    provider = (
        JevDecisionProvider(api_key=load_jev_api_key(env_path))
        if provider_mode == "live"
        else FixedDecisionProvider(action.id)
    )
    target = subprocess.Popen([str(target_path)])
    bridge = JsonLinesBridgeClient([str(host_path)], timeout_seconds=5)
    try:
        await wait_for_owned_foreground(target)
        runtime = BridgeRuntimeAdapter(bridge)
        orchestrator = AgentOrchestrator(
            registry=registry,
            provider=provider,
            runtime=runtime,
            policy=PolicyGate(ApprovalService()),
            journal=ExecutionJournal(journal_root / session_id),
            minimum_confidence=0.8,
        )
        result = await orchestrator.run_next(
            goal=(
                "Enter the approved DEMO_APPROVED marker into the owned synthetic target "
                "only if the registered action is safe now."
            ),
            idempotency_key=f"{session_id}:step-1",
        )
        final_observation = await runtime.observe()
        if hold_seconds:
            await asyncio.sleep(hold_seconds)
        return {
            "provider": provider_mode.upper(),
            "status": result.status.value,
            "action_id": result.action_id,
            "effect_observed": final_observation.facts.get("required_text_present") is True,
            "journal_state": (
                orchestrator.journal.state(f"{session_id}:step-1").value
                if orchestrator.journal.state(f"{session_id}:step-1") is not None
                else None
            ),
        }
    finally:
        close_provider = getattr(provider, "close", None)
        if close_provider is not None:
            close_provider()
        await bridge.close()
        if target.poll() is None:
            target.terminate()
            try:
                target.wait(timeout=2)
            except subprocess.TimeoutExpired:
                target.kill()
                target.wait(timeout=2)


def main() -> None:
    repository_root = Path(__file__).parents[2]
    bin_directory = repository_root / "windows-host" / "bin"
    parser = argparse.ArgumentParser(
        description="Run the policy-gated Jev-to-Windows Host integration demo."
    )
    parser.add_argument("--provider", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--hold-seconds", type=float, default=0)
    parser.add_argument("--env-file", type=Path, default=repository_root / ".env")
    parser.add_argument(
        "--host",
        type=Path,
        default=bin_directory / "AutomateJev.WindowsHost.exe",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=bin_directory / "AutomateJev.SmokeTarget.exe",
    )
    parser.add_argument(
        "--journal-root",
        type=Path,
        default=repository_root / ".automate-jev" / "integrated-demo",
    )
    arguments = parser.parse_args()
    result = asyncio.run(
        run_integrated_demo(
            host_path=arguments.host,
            target_path=arguments.target,
            journal_root=arguments.journal_root,
            provider_mode=arguments.provider,
            env_path=arguments.env_file,
            hold_seconds=arguments.hold_seconds,
        )
    )
    print(result)


if __name__ == "__main__":
    main()
