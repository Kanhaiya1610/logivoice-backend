"""LogiVoice backend application factory."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import default_config
from .errors import register_error_handlers
from .extensions import db, migrate
from .api.v1 import api_v1
from .services.storage import build_storage


def _configure_logging(app: Flask) -> None:
    """Configure concise JSON logs suitable for local and Render deployment."""

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            import json
            from datetime import datetime, timezone

            return json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                },
                ensure_ascii=False,
            )

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").handlers.clear()
    logging.getLogger("werkzeug").addHandler(handler)


def create_app(config: dict | None = None) -> Flask:
    """Create and configure a LogiVoice Flask application.

    The factory intentionally does not create production tables automatically.
    Production deployments must run ``flask db upgrade`` before serving traffic.
    Tests can create isolated tables explicitly with ``db.create_all()``.
    """

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - dependency is in requirements.txt
        pass

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(default_config(Path(app.instance_path)))
    if config:
        app.config.from_mapping(config)

    origins = app.config.get("CORS_ORIGINS", [])
    if isinstance(origins, str):
        origins = [origin.strip() for origin in origins.split(",") if origin.strip()]
        app.config["CORS_ORIGINS"] = origins
    if not origins or "*" in origins:
        raise RuntimeError("CORS_ORIGINS must contain one or more explicit origins")
    if app.config.get("APP_ENV") == "production" and app.config.get("STORAGE_BACKEND") != "s3":
        raise RuntimeError("Production deployments must use S3-compatible persistent storage")
    if app.config.get("APP_ENV") == "production" and app.config.get("SECRET_KEY") == "local-development-only-change-me":
        raise RuntimeError("SECRET_KEY must be changed in production")
    if app.config.get("APP_ENV") == "production":
        local_origins = {"http://localhost", "http://localhost:5173", "http://127.0.0.1", "http://127.0.0.1:5173"}
        if not any(origin not in local_origins for origin in origins):
            raise RuntimeError("Production CORS_ORIGINS must contain the deployed frontend origin, not only localhost")

    _configure_logging(app)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    app.extensions["storage"] = build_storage(app)

    CORS(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        supports_credentials=False,
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "DELETE", "OPTIONS"],
    )

    app.register_blueprint(api_v1, url_prefix="/api/v1")
    register_error_handlers(app)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    return app
