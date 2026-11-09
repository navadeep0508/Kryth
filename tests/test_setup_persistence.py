from pathlib import Path

import config.keyring_store as keyring_store
import config.loader as loader
from config.config import Config
from config.setup_wizard import SetupWizard


def test_completed_setup_is_restored_on_next_launch(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.toml"
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(keyring_store, "KEYRING_AVAILABLE", False)
    monkeypatch.setattr(keyring_store, "_kryth_path", lambda: credentials_path)
    monkeypatch.setattr(keyring_store, "_legacy_path", lambda: credentials_path)
    monkeypatch.setattr(loader, "get_system_config_path", lambda: config_path)
    monkeypatch.setattr(loader, "_get_project_config", lambda _cwd: None)

    setup_config = Config(cwd=tmp_path)
    SetupWizard(setup_config).save_setup(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        model="test/model",
    )

    restarted_config = loader.load_config(cwd=tmp_path)

    assert restarted_config.providers.active_name == "openrouter"
    assert restarted_config.model.name == "test/model"
    assert restarted_config.api_key == "test-key"
    assert not SetupWizard(restarted_config).is_first_launch()
