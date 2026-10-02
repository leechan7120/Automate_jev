import pytest

from automate_jev.models import Observation
from automate_jev.verifier import VerificationStatus, verify_until_stable


@pytest.mark.asyncio
async def test_verifier_requires_two_stable_observations():
    calls = 0

    async def observe():
        nonlocal calls
        calls += 1
        return Observation.capture("desktop", {"done": calls >= 2})

    status = await verify_until_stable(
        observe,
        ({"type": "fact_equals", "key": "done", "value": True},),
        deadline_seconds=0.5,
        schedule=(0.001,),
    )
    assert status is VerificationStatus.SUCCESS
    assert calls >= 3


@pytest.mark.asyncio
async def test_verifier_returns_unknown_without_reexecution():
    calls = 0

    async def observe():
        nonlocal calls
        calls += 1
        return Observation.capture("desktop", {"done": False})

    status = await verify_until_stable(
        observe,
        ({"type": "fact_equals", "key": "done", "value": True},),
        deadline_seconds=0.1,
        schedule=(0.001,),
    )
    assert status is VerificationStatus.UNKNOWN
    assert calls > 1

