import os
from pathlib import Path

import pytest

from automate_jev.bridge import BridgeError, JsonLinesBridgeClient
from automate_jev.bridge_runtime import BridgeRuntimeAdapter
from automate_jev.models import Action, Risk
from automate_jev.registry import ActionRegistry


HOST = Path(__file__).parents[1] / "windows-host" / "bin" / "AutomateJev.WindowsHost.exe"


@pytest.mark.skipif(os.name != "nt" or not HOST.exists(), reason="compiled Windows Host unavailable")
@pytest.mark.asyncio
async def test_compiled_windows_host_observes_and_rejects_unimplemented_action():
    bridge = JsonLinesBridgeClient([str(HOST)], timeout_seconds=3)
    runtime = BridgeRuntimeAdapter(bridge)
    try:
        observation = await runtime.observe()
        assert observation.state_revision.startswith("sha256:")
        action = Action(
            id="demo.unimplemented",
            domain="desktop",
            verb="invoke",
            description="This action must be rejected by the native policy.",
            target={"process": "notepad.exe"},
            expected_effects=({
                "type": "fact_equals",
                "key": "done",
                "value": True,
                "risk_effect": "fill_demo_text",
            },),
            risk=Risk.SAFE,
        )
        registry = ActionRegistry("native-host-test")
        snapshot = registry.replace([action])
        await runtime.register(snapshot)
        with pytest.raises(BridgeError) as registration_error:
            await runtime.register(snapshot)
        assert registration_error.value.code == "INVALID_ACTION"
        envelope = registry.envelope(action.id, observation, "run-1:step-1")
        with pytest.raises(BridgeError) as error:
            await bridge.call(
                "execute_registered",
                {
                    "session_id": envelope.session_id,
                    "registry_version": envelope.registry_version,
                    "action_id": envelope.action.id,
                    "action_hash": envelope.action_hash,
                    "expires_at": envelope.expires_at,
                    "idempotency_key": envelope.idempotency_key,
                    "expected_state_revision": envelope.expected_state_revision,
                    "target": dict(envelope.action.target),
                    "arguments": {},
                    "preconditions": [],
                    "expected_effects": [],
                },
            )
        assert error.value.code == "POLICY_BLOCKED"
    finally:
        await bridge.close()
