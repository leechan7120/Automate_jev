from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .env import load_jev_api_key
from .jev_provider import JevDecisionProvider
from .models import Observation


async def smoke(env_path: Path) -> None:
    provider = JevDecisionProvider(api_key=load_jev_api_key(env_path))
    try:
        decision = await provider.choose(
            goal="Select the only safe demo action.",
            observation=Observation.capture(
                "desktop",
                {"demo_ready": True, "contains_real_user_data": False},
            ),
            candidates={
                "demo.wait": "Wait safely for the deterministic demo state.",
                "abstain": "No safe executable action is available.",
            },
        )
        print({
            "provider": "LIVE",
            "model": provider.model,
            "action_id": decision.action_id,
            "confidence": round(decision.confidence, 4),
        })
    finally:
        provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a redacted live Jev decision smoke test.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    arguments = parser.parse_args()
    asyncio.run(smoke(arguments.env_file))


if __name__ == "__main__":
    main()
