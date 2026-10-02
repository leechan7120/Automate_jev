from __future__ import annotations

import os
from pathlib import Path

from .models import ContractError


KEY_NAMES = ("JEV_API_KEY", "TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY", "jev_api_key")
GEMINI_KEY_NAMES = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "gemini_api_key",
    "jev_PJ_Gemini_Key",
)
GEMINI_MODEL_NAMES = ("GEMINI_MODEL", "gemini_model")


def _env_file_values(env_path: str | Path) -> dict[str, str]:
    path = Path(env_path)
    if not path.is_file():
        raise ContractError(f"configuration is missing and {path} does not exist")
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
    return values


def load_jev_api_key(env_path: str | Path = ".env") -> str:
    """Load a Jev key without mutating process-wide environment variables."""
    for name in KEY_NAMES[:-1]:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()

    values = _env_file_values(env_path)

    for name in KEY_NAMES:
        value = values.get(name)
        if value and value.strip():
            return value.strip()
    raise ContractError("Jev API key is missing from the env file")


def load_gemini_settings(env_path: str | Path = ".env") -> tuple[str, str]:
    key = next(
        (os.environ[name].strip() for name in GEMINI_KEY_NAMES[:-1] if os.environ.get(name, "").strip()),
        None,
    )
    model = next(
        (os.environ[name].strip() for name in GEMINI_MODEL_NAMES[:-1] if os.environ.get(name, "").strip()),
        None,
    )
    if key and model:
        return key, model
    values = _env_file_values(env_path)
    key = key or next((values[name].strip() for name in GEMINI_KEY_NAMES if values.get(name, "").strip()), None)
    model = model or next((values[name].strip() for name in GEMINI_MODEL_NAMES if values.get(name, "").strip()), None)
    if not key:
        raise ContractError("Gemini API key is missing")
    if not model or len(model) > 128:
        raise ContractError("Gemini model must be configured with 1 to 128 characters")
    return key, model
