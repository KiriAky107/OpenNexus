from pathlib import Path
import asyncio
import tomllib

from app import config


def test_settings_preserve_project_prerelease_in_frozen_resource_layout(monkeypatch, tmp_path):
    resource_root = tmp_path / "_internal"
    resource_root.mkdir()
    (resource_root / "pyproject.toml").write_text(
        '[project]\nversion = "0.6.0-beta2"\n', encoding="utf-8"
    )
    monkeypatch.setattr(config, "BACKEND_DIR", resource_root)
    monkeypatch.delenv("APP_VERSION", raising=False)
    config.get_settings.cache_clear()
    assert config.get_settings().version == "0.6.0-beta2"
    monkeypatch.setenv("APP_VERSION", "0.6.1-alpha1")
    config.get_settings.cache_clear()
    assert config.get_settings().version == "0.6.1-alpha1"


def test_core_status_uses_the_project_release_version():
    from app.main import service_status

    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert asyncio.run(service_status()).version == tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"]
