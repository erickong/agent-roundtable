import os

import pytest

from config import load_config


def _clear_config_env(monkeypatch: pytest.MonkeyPatch):
    for key in list(os.environ):
        if key.startswith("MODERATOR_LLM_") or key.startswith("LLM_PROVIDER_") or key == "TAVILY_API_KEY":
            monkeypatch.delenv(key, raising=False)


def test_missing_explicit_env_file_raises(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _clear_config_env(monkeypatch)

    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "missing.env"))


def test_load_config_from_environment_without_dotenv_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _clear_config_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MODERATOR_LLM_API_KEY", "moderator-key")
    monkeypatch.setenv("MODERATOR_LLM_BASE_URL", "https://example.com")
    monkeypatch.setenv("MODERATOR_LLM_MODEL", "moderator-model")
    monkeypatch.setenv("LLM_PROVIDER_1_API_KEY", "expert-key")
    monkeypatch.setenv("LLM_PROVIDER_1_BASE_URL", "https://example.com")
    monkeypatch.setenv("LLM_PROVIDER_1_MODEL", "expert-model")

    config = load_config()

    assert config.moderator_llm.model == "moderator-model"
    assert len(config.expert_providers) == 1
    assert config.expert_providers[0].model == "expert-model"