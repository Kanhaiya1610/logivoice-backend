"""Environment-driven configuration for the backend."""

from __future__ import annotations

import os
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _env(name: str, default: str = "") -> str:
    """Read a string environment variable, treating blank values as missing."""

    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _cors_origins() -> list[str]:
    raw = _env(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _database_url(instance_path: Path) -> str:
    configured = _env("DATABASE_URL")
    if configured:
        # Render commonly supplies postgres:// while SQLAlchemy 2 expects an
        # explicit driver when psycopg 3 is installed.
        if configured.startswith("postgres://"):
            return "postgresql+psycopg://" + configured[len("postgres://") :]
        if configured.startswith("postgresql://"):
            return "postgresql+psycopg://" + configured[len("postgresql://") :]
        return configured
    return f"sqlite:///{(instance_path / 'logivoice.db').as_posix()}"


def default_config(instance_path: Path) -> dict:
    """Return fresh defaults so tests can change environment between apps."""

    # Flask's instance folder is ignored by git and is therefore absent on a
    # clean CI checkout. Create it before constructing the default SQLite URL.
    instance_path.mkdir(parents=True, exist_ok=True)

    environment = _env("APP_ENV", _env("FLASK_ENV", "development")).lower()
    upload_mb = _as_int(os.getenv("MAX_UPLOAD_SIZE_MB"), 25)
    storage_root = _env("STORAGE_ROOT", str(instance_path / "storage"))
    return {
        "APP_ENV": environment,
        "SECRET_KEY": _env("SECRET_KEY", "local-development-only-change-me"),
        "SQLALCHEMY_DATABASE_URI": _database_url(instance_path),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "MAX_CONTENT_LENGTH": upload_mb * 1024 * 1024,
        "MAX_UPLOAD_SIZE_BYTES": upload_mb * 1024 * 1024,
        "CORS_ORIGINS": _cors_origins(),
        "TOKEN_MAX_AGE_SECONDS": _as_int(os.getenv("TOKEN_MAX_AGE_SECONDS"), 3600),
        "STORAGE_BACKEND": _env("STORAGE_BACKEND", "local").lower(),
        "STORAGE_ROOT": storage_root,
        "S3_BUCKET": _env("S3_BUCKET"),
        "S3_REGION": _env("S3_REGION"),
        "S3_ENDPOINT_URL": _env("S3_ENDPOINT_URL"),
        "S3_ACCESS_KEY_ID": _env("S3_ACCESS_KEY_ID"),
        "S3_SECRET_ACCESS_KEY": _env("S3_SECRET_ACCESS_KEY"),
        "GEMINI_API_KEY": _env("GEMINI_API_KEY"),
        "GEMINI_MODEL": _env("GEMINI_MODEL", "gemini-1.5-flash"),
        "TESSERACT_PATH": _env("TESSERACT_PATH"),
        "JSON_SORT_KEYS": False,
    }
