import pytest

from automate_jev.env import (
    GEMINI_KEY_NAMES,
    GEMINI_MODEL_NAMES,
    KEY_NAMES,
    load_gemini_settings,
    load_jev_api_key,
)
from automate_jev.models import ContractError


def clear_key_environment(monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_loads_lowercase_key_from_env_file(tmp_path, monkeypatch):
    clear_key_environment(monkeypatch)
    path = tmp_path / ".env"
    path.write_text("# local only\njev_api_key=test-value\n", encoding="utf-8")
    assert load_jev_api_key(path) == "test-value"


def test_process_environment_takes_precedence(tmp_path, monkeypatch):
    clear_key_environment(monkeypatch)
    monkeypatch.setenv("JEV_API_KEY", "process-value")
    path = tmp_path / ".env"
    path.write_text("jev_api_key=file-value\n", encoding="utf-8")
    assert load_jev_api_key(path) == "process-value"


def test_missing_key_fails_without_echoing_file_contents(tmp_path, monkeypatch):
    clear_key_environment(monkeypatch)
    path = tmp_path / ".env"
    path.write_text("unrelated=sensitive-value\n", encoding="utf-8")
    with pytest.raises(ContractError) as error:
        load_jev_api_key(path)
    assert "sensitive-value" not in str(error.value)


def test_gemini_key_and_model_are_both_required(tmp_path, monkeypatch):
    for name in (*GEMINI_KEY_NAMES, *GEMINI_MODEL_NAMES):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "gemini_api_key=gemini-test-key\ngemini_model=available-video-model\n",
        encoding="utf-8",
    )
    assert load_gemini_settings(path) == ("gemini-test-key", "available-video-model")

    path.write_text("gemini_api_key=gemini-test-key\n", encoding="utf-8")
    with pytest.raises(ContractError, match="model must be configured"):
        load_gemini_settings(path)


def test_gemini_project_key_alias_is_supported(tmp_path, monkeypatch):
    for name in (*GEMINI_KEY_NAMES, *GEMINI_MODEL_NAMES):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "jev_PJ_Gemini_Key=project-key\ngemini_model=available-video-model\n",
        encoding="utf-8",
    )
    assert load_gemini_settings(path) == ("project-key", "available-video-model")
