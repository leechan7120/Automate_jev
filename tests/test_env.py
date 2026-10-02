import pytest

from automate_jev.env import KEY_NAMES, load_jev_api_key
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
