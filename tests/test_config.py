from __future__ import annotations

from app.config import default_config


def test_default_config_is_safe_on_a_clean_checkout(tmp_path, monkeypatch):
    for name in (
        "APP_ENV",
        "FLASK_ENV",
        "SECRET_KEY",
        "DATABASE_URL",
        "CORS_ORIGINS",
        "STORAGE_BACKEND",
        "STORAGE_ROOT",
        "TESSERACT_PATH",
    ):
        monkeypatch.setenv(name, "")

    instance_path = tmp_path / "missing-instance"
    config = default_config(instance_path)

    assert instance_path.is_dir()
    assert config["APP_ENV"] == "development"
    assert config["SECRET_KEY"] == "local-development-only-change-me"
    assert config["SQLALCHEMY_DATABASE_URI"].endswith("missing-instance/logivoice.db")
    assert config["CORS_ORIGINS"] == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert config["STORAGE_BACKEND"] == "local"
    assert config["TESSERACT_PATH"] == ""
    assert config["NLP_MODEL"] == "en_core_web_sm"
