from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ActionEnvelope, ContractError, Decision, Observation


@dataclass(slots=True)
class FixedDecisionProvider:
    action_id: str
    confidence: float = 1.0

    async def choose(self, **_: Any) -> Decision:
        return Decision(self.action_id, self.confidence)


class SimulatedRuntime:
    """Deterministic adapter used for safety tests and the credential-free demo."""

    def __init__(self, facts: dict[str, Any], *, surface: str = "desktop") -> None:
        self.facts = dict(facts)
        self.surface = surface
        self.executions = 0

    async def observe(self) -> Observation:
        return Observation.capture(self.surface, self.facts)

    async def execute_if_current(self, envelope: ActionEnvelope) -> None:
        current = await self.observe()
        if current.state_revision != envelope.expected_state_revision:
            raise ContractError("state revision mismatch")
        if envelope.is_expired():
            raise ContractError("action expired")
        self.executions += 1
        for effect in envelope.action.expected_effects:
            if effect.get("type") != "fact_equals":
                raise ContractError("unsupported simulated effect")
            self.facts[str(effect["key"])] = effect.get("value")

