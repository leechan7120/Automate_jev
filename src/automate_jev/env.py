from __future__ import annotations

import os
from pathlib import Path

from .models import ContractError


KEY_NAMES = ("JEV_API_KEY", "TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY", "jev_api_key")


def load_jev_api_key(env_path: str | Path = ".env") -> str:
    """Load a Jev key without mutating process-wide environment variables."""
    for name in KEY_NAMES[:-1]:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()

    path = Path(env_path)
    if not path.is_file():
        raise ContractError(f"Jev API key is not configured and {path} does not exist")
    if path.stat().st_size > 65_536:
        raise ContractError("env file exceeds 64 KiB")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ContractError(f"invalid env entry on line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value

    for name in KEY_NAMES:
        value = values.get(name)
        if value and value.strip():
            return value.strip()
    raise ContractError("Jev API key is missing from the env file")
