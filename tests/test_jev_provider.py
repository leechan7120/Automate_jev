from types import SimpleNamespace

import pytest

from automate_jev.jev_provider import JevDecisionProvider, JevProviderError
from automate_jev.models import Observation


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.closed = False

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=None, answers={"next_action": self.answer})

    def close(self):
        self.closed = True


def answer(action_id="demo.wait", confidence=0.9, probabilities=None):
    return SimpleNamespace(
        choice=action_id,
        confidence=confidence,
        probabilities=probabilities or {action_id: confidence},
    )


def provider_with(fake):
    created = []

    def factory(api_key, base_url, timeout):
        assert api_key == "test-key"
        created.append((base_url, timeout))
        return fake

    return JevDecisionProvider(api_key="test-key", client_factory=factory), created


@pytest.mark.asyncio
async def test_live_provider_reuses_client_and_redacts_state():
    fake = FakeClient(answer())
    provider, created = provider_with(fake)
    observation = Observation.capture(
        "desktop",
        {"ready": True, "api_key": "observation-secret"},
    )
    candidates = {
        "demo.wait": "Wait safely.",
        "abstain": "No safe action.",
    }
    first = await provider.choose(
        goal="password=goal-secret choose safely",
        observation=observation,
        candidates=candidates,
    )
    second = await provider.choose(
        goal="choose safely",
        observation=observation,
        candidates=candidates,
    )
    assert first.action_id == second.action_id == "demo.wait"
    assert first.confidence == 0.9
    assert len(created) == 1
    assert len(fake.calls) == 2
    sent_state = fake.calls[0]["state"]
    assert sent_state["observation"]["facts"]["api_key"] == "[REDACTED]"
    assert "goal-secret" not in sent_state["goal"]
    provider.close()
    assert fake.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_answer",
    [
        answer(action_id="invented.action"),
        answer(confidence=-0.1),
        answer(confidence=1.1),
        answer(confidence=None),
        answer(probabilities={"invented.action": 1.0}),
    ],
)
async def test_invalid_provider_response_fails_closed(bad_answer):
    provider, _ = provider_with(FakeClient(bad_answer))
    with pytest.raises(JevProviderError) as error:
        await provider.choose(
            goal="choose safely",
            observation=Observation.capture("desktop", {"ready": True}),
            candidates={"demo.wait": "Wait safely.", "abstain": "Stop."},
        )
    assert error.value.code == "invalid_response"


def test_provider_repr_does_not_expose_key():
    provider = JevDecisionProvider(api_key="super-secret-key")
    assert "super-secret-key" not in repr(provider)
