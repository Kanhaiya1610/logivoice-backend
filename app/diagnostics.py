"""Structured, content-free diagnostics for application and chat requests."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter
from uuid import uuid4

from flask import g, has_request_context, request


DIAGNOSTIC_FIELDS = frozenset({
    "request_id", "provider", "model", "reason", "provider_status", "error_type",
    "retryable", "route", "method", "http_status", "duration_ms", "timeout_seconds",
    "api_key_configured", "sdk_version",
})


def safe_model_name(config) -> str:
    """Only model identifiers, never arbitrary environment values, enter logs."""
    name = config.get("GEMINI_MODEL", "gemini-2.5-flash")
    if isinstance(name, str) and re.fullmatch(r"(?:models/)?[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        return name
    return "invalid_model_configuration"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        body = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if has_request_context():
            body["request_id"] = getattr(g, "request_id", None)
        # Do not serialize arbitrary extras, exception messages, traceback locals,
        # request bodies, headers, URLs, prompts, credentials, or provider responses.
        fields = getattr(record, "diagnostic", {})
        body.update({key: value for key, value in fields.items() if key in DIAGNOSTIC_FIELDS})
        return json.dumps(body, ensure_ascii=False)


def configure_logging(app) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    logging.getLogger("werkzeug").handlers.clear()
    logging.getLogger("werkzeug").addHandler(handler)


def install_request_diagnostics(app) -> None:
    @app.before_request
    def start_request():
        # Generate rather than trusting caller-supplied IDs or arbitrary log text.
        g.request_id = uuid4().hex
        g.request_started_at = perf_counter()
        if request.endpoint == "api_v1.chat":
            app.logger.info("chat_request_started", extra={"diagnostic": {
                "route": "/api/v1/chat", "method": request.method,
            }})

    @app.after_request
    def finish_request(response):
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        # Keep successful Render health probes from drowning out useful events.
        if request.endpoint == "api_v1.health" and response.status_code < 400:
            return response
        started = getattr(g, "request_started_at", perf_counter())
        app.logger.info("request_completed", extra={"diagnostic": {
            "request_id": request_id,
            "route": request.url_rule.rule if request.url_rule else "<unmatched>",
            "method": request.method,
            "http_status": response.status_code,
            "duration_ms": round(max(0, perf_counter() - started) * 1000, 2),
        }})
        return response


def log_chat_configuration(app) -> None:
    # Package metadata is safe to inspect; importing the SDK or contacting Gemini
    # at startup is not necessary and would make unrelated features less reliable.
    try:
        sdk_version = version("google-genai")
    except PackageNotFoundError:
        sdk_version = "not_installed"
    app.logger.info("chat_provider_configuration", extra={"diagnostic": {
        "provider": "gemini",
        "model": safe_model_name(app.config),
        "api_key_configured": bool(app.config.get("GEMINI_API_KEY")),
        "sdk_version": sdk_version,
        "timeout_seconds": app.config["GEMINI_TIMEOUT_SECONDS"],
    }})
