import sys

import pytest

from automate_jev.bridge import JsonLinesBridgeClient
from automate_jev.bridge_runtime import BridgeRuntimeAdapter
from automate_jev.models import Action, Risk
from automate_jev.registry import ActionRegistry


@pytest.mark.asyncio
async def test_json_lines_bridge_observe_and_execute_round_trip():
    bridge = JsonLinesBridgeClient(
        [sys.executable, "-m", "automate_jev.mock_bridge"],
        timeout_seconds=3,
    )
    runtime = BridgeRuntimeAdapter(bridge)
    try:
        before = await runtime.observe()
        assert before.facts["required_text_present"] is False
        action = Action(
            id="notepad.fill-required-text",
            domain="desktop",
            verb="fill",
            description="Fill approved demo text.",
            arguments={"text": "DEMO_APPROVED"},
            expected_effects=({
                "type": "fact_equals",
                "key": "required_text_present",
                "value": True,
                "risk_effect": "fill_demo_text",
            },),
            risk=Risk.SAFE,
        )
        registry = ActionRegistry("bridge-test")
        registry.replace([action])
        envelope = registry.envelope(action.id, before, "run-1:step-1")
        await runtime.execute_if_current(envelope)
        after = await runtime.observe()
        assert after.facts["required_text_present"] is True
        assert after.state_revision != before.state_revision
    finally:
        await bridge.close()


@pytest.mark.asyncio
async def test_bridge_rejects_stale_revision_without_effect():
    bridge = JsonLinesBridgeClient([sys.executable, "-m", "automate_jev.mock_bridge"])
    runtime = BridgeRuntimeAdapter(bridge)
    try:
        before = await runtime.observe()
        action = Action(
            id="demo.fill",
            domain="desktop",
            verb="fill",
            description="Fill approved demo text.",
            expected_effects=({
                "type": "fact_equals",
                "key": "required_text_present",
                "value": True,
                "risk_effect": "fill_demo_text",
            },),
            risk=Risk.SAFE,
        )
        registry = ActionRegistry("bridge-test")
        registry.replace([action])
        stale = registry.envelope(action.id, before, "run-1:step-1")
        await bridge.call("execute_registered", {
            "expected_state_revision": before.state_revision,
            "expected_effects": [{"type": "fact_equals", "key": "external", "value": True}],
        })
        with pytest.raises(Exception, match="revision mismatch"):
            await runtime.execute_if_current(stale)
        after = await runtime.observe()
        assert after.facts["required_text_present"] is False
    finally:
        await bridge.close()
