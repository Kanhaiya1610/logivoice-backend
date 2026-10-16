"""Regression contract for safe, lazy Gemini diagnostics (no real SDK or credentials)."""

from __future__ import annotations

import io
import builtins
import json
import logging
import math
import re
import sys
from types import ModuleType, SimpleNamespace
from http import HTTPStatus

import pytest

from app.auth import create_access_token
from app.extensions import db
from app.models import Document, User
from app.services import chat

from .conftest import headers


API_KEY = "dummy-gemini-key-for-regression-only"
PROVIDER_TOKEN = "dummy-provider-token-not-a-real-secret"
MODEL = "diagnostics-test-model"
DOCUMENT_TEXT = "PRIVATE_DOCUMENT_SENTINEL: invoice DIAG-42 has a total of 42."
QUESTION = "PRIVATE_QUESTION_SENTINEL: what is the invoice total?"
BODY_MARKER = "PRIVATE_REQUEST_BODY_SENTINEL"
RAW_PROVIDER_MESSAGE = (
    f"FAKE_RAW_PROVIDER_MESSAGE token={PROVIDER_TOKEN} key={API_KEY} "
    f"context={DOCUMENT_TEXT} question={QUESTION}"
)


class FakeProviderError(RuntimeError):
    """Match the numeric code exposed by Google API provider exceptions."""

    def __init__(self, message, status):
        super().__init__(message)
        self.code = status


FAILURES = [
    pytest.param(FakeProviderError, 404, "model_not_found", False, 502, id="model-not-found"),
    pytest.param(FakeProviderError, 400, "invalid_request", False, 502, id="invalid-request"),
    pytest.param(FakeProviderError, 401, "authentication_failed", False, 502, id="authentication"),
    pytest.param(FakeProviderError, 403, "permission_denied", False, 502, id="permission"),
    pytest.param(FakeProviderError, 429, "quota_exceeded", True, 503, id="quota"),
    pytest.param(TimeoutError, None, "timeout", True, 504, id="python-timeout"),
    pytest.param(FakeProviderError, 504, "timeout", True, 504, id="provider-timeout"),
    pytest.param(FakeProviderError, 503, "service_unavailable", True, 503, id="unavailable"),
    pytest.param(FakeProviderError, 500, "provider_error", False, 502, id="other-status"),
    pytest.param(FakeProviderError, None, "provider_error", False, 502, id="no-status"),
    pytest.param(ValueError, None, "provider_error", False, 502, id="unclassified-exception"),
]


def _provider_error(error_type, status):
    if error_type is FakeProviderError:
        return FakeProviderError(RAW_PROVIDER_MESSAGE, status)
    return error_type(RAW_PROVIDER_MESSAGE)


@pytest.fixture(autouse=True)
def fake_genai(monkeypatch):
    """Install the supported SDK shape so no real provider is called."""
    state = SimpleNamespace(
        configured=[], models=[], requests=[], clients=[], closed=0,
        error=None, answer="  Total: 42.  ", http_options=[], retry_options=[]
    )
    google = ModuleType("google")
    google.__path__ = []
    genai = ModuleType("google.genai")
    types = ModuleType("google.genai.types")

    class HttpRetryOptions:
        def __init__(self, **kwargs):
            state.retry_options.append(kwargs)
            self.__dict__.update(kwargs)

    class HttpOptions:
        def __init__(self, **kwargs):
            state.http_options.append(kwargs)
            self.__dict__.update(kwargs)

    def client_factory(**kwargs):
        state.configured.append({"api_key": kwargs["api_key"]})
        state.clients.append(kwargs)

        def generate_content(**request):
            state.models.append(request["model"])
            state.requests.append({
                "prompt": request["contents"],
                "options": {"http_options": kwargs["http_options"]},
            })
            if state.error is not None:
                raise state.error
            return SimpleNamespace(text=state.answer)

        def close():
            state.closed += 1

        return SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
            close=close,
        )

    genai.Client = client_factory
    types.HttpOptions = HttpOptions
    types.HttpRetryOptions = HttpRetryOptions
    genai.types = types
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", types)
    return state


@pytest.fixture()
def formatted_logs(app, monkeypatch, caplog):
    """Exercise the application's actual handler and JsonFormatter, not just caplog."""
    stream = io.StringIO()
    handlers = [handler for handler in app.logger.handlers if isinstance(handler, logging.StreamHandler)]
    assert handlers, "The app must have a stream handler for structured diagnostics"
    for handler in handlers:
        monkeypatch.setattr(handler, "stream", stream)
    caplog.set_level(logging.DEBUG, logger=app.logger.name)
    return stream


@pytest.fixture()
def chat_documents(app):
    # Seed real auth/ownership data without spending process-global registration limits.
    with app.app_context():
        owner = User(email="diagnostics-owner@example.test", password_hash="unused-test-hash")
        other = User(email="diagnostics-other@example.test", password_hash="unused-test-hash")
        db.session.add_all([owner, other])
        db.session.flush()

        def document(user, name, text):
            return Document(
                owner_id=user.id,
                original_filename=f"{name}.txt",
                storage_key=f"diagnostics/{name}.txt",
                mime_type="text/plain",
                file_size=len(text.encode("utf-8")),
                status="processed",
                extracted_text=text,
            )

        owned = document(owner, "owned", DOCUMENT_TEXT)
        empty = document(owner, "empty", " \n\t ")
        foreign = document(other, "foreign", DOCUMENT_TEXT)
        db.session.add_all([owned, empty, foreign])
        db.session.commit()
        return SimpleNamespace(
            token=create_access_token(owner),
            owned=owned.id,
            empty=empty.id,
            foreign=foreign.id,
        )


def _assert_redacted(*outputs, extra=()):
    for output in outputs:
        for private in (
            API_KEY, PROVIDER_TOKEN, DOCUMENT_TEXT, QUESTION, BODY_MARKER,
            RAW_PROVIDER_MESSAGE, *extra,
        ):
            assert private not in output
        assert not re.search(r"\bauthorization\b[\"']?\s*[:=]", output, re.IGNORECASE)


def _json_logs(stream):
    lines = stream.getvalue().splitlines()
    assert lines, "Expected JSON output from the application's handler"
    records = [json.loads(line) for line in lines if line.strip()]
    # The contract allows diagnostic fields to be merged or kept in a diagnostic object.
    return [{**record, **record.get("diagnostic", {})} for record in records]


def _event(records, name, request_id):
    matches = [
        record for record in records
        if record.get("event", record.get("message")) == name
        and record.get("request_id") == request_id
    ]
    assert len(matches) == 1, f"Expected one correlated {name} event; got {records!r}"
    return matches[0]


def _request_id(response):
    request_id = response.headers.get("X-Request-ID")
    assert request_id and request_id.strip(), "Server must expose its generated request ID"
    assert request_id != "untrusted-client-request-id"
    exposed = response.headers.get("Access-Control-Expose-Headers", "")
    assert "x-request-id" in {name.strip().lower() for name in exposed.split(",")}
    assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
    return request_id


def _completed(records, request_id, status):
    event = _event(records, "request_completed", request_id)
    assert event["route"] == "/api/v1/chat"
    assert event["method"] == "POST"
    assert event["http_status"] == status
    duration = event["duration_ms"]
    assert isinstance(duration, (int, float)) and not isinstance(duration, bool)
    assert math.isfinite(duration) and duration >= 0


def _post_chat(client, token, document_id):
    return client.post(
        "/api/v1/chat",
        json={"document_id": document_id, "question": QUESTION, "client_note": BODY_MARKER},
        headers={
            **headers(token),
            "Origin": "http://localhost:5173",
            "X-Request-ID": "untrusted-client-request-id",
        },
    )


@pytest.mark.parametrize("timeout", [None, 7.25], ids=["default-timeout", "configured-timeout"])
def test_answer_is_grounded_and_applies_timeout_without_logging_content(
    timeout, fake_genai, formatted_logs, caplog,
):
    config = {"GEMINI_API_KEY": API_KEY, "GEMINI_MODEL": MODEL}
    if timeout is not None:
        # The timeout configuration name is an explicit assumption of this contract.
        config["GEMINI_TIMEOUT_SECONDS"] = timeout

    answer = chat.answer_question(DOCUMENT_TEXT, QUESTION, config)

    assert answer == "Total: 42."
    assert fake_genai.configured == [{"api_key": API_KEY}]
    assert fake_genai.models == [MODEL]
    assert len(fake_genai.requests) == 1
    sent = fake_genai.requests[0]
    assert DOCUMENT_TEXT in sent["prompt"]
    assert QUESTION in sent["prompt"]
    assert "only the document context" in sent["prompt"].lower()
    assert "not found" in sent["prompt"].lower()
    assert sent["options"]["http_options"].timeout == int((20 if timeout is None else timeout) * 1000)
    assert sent["options"]["http_options"].retry_options.attempts == 1
    assert fake_genai.closed == 1
    _assert_redacted(formatted_logs.getvalue(), caplog.text, extra=(sent["prompt"],))


@pytest.mark.parametrize("error_type,status,reason,retryable,http_status", FAILURES)
def test_provider_error_taxonomy_and_wrapper_redaction(
    error_type, status, reason, retryable, http_status, fake_genai, formatted_logs, caplog,
):
    fake_genai.error = _provider_error(error_type, status)

    with pytest.raises(RuntimeError) as caught:
        chat.answer_question(DOCUMENT_TEXT, QUESTION, {"GEMINI_API_KEY": API_KEY})

    wrapper_type = getattr(chat, "ChatProviderError", None)
    assert wrapper_type is not None, "Provider failures need a typed ChatProviderError wrapper"
    assert issubclass(wrapper_type, RuntimeError)
    error = caught.value
    assert isinstance(error, wrapper_type)
    assert error.reason == reason
    assert error.provider_status == status
    assert error.provider_status is None or type(error.provider_status) is int
    assert error.exception_type == error_type.__name__
    assert error.retryable is retryable
    assert str(error).strip()
    _assert_redacted(str(error), formatted_logs.getvalue(), caplog.text)


def test_missing_key_does_not_require_or_call_the_lazy_sdk(monkeypatch, fake_genai):
    # A missing key must be detected even if importing the optional SDK would fail.
    monkeypatch.setitem(sys.modules, "google.genai", None)
    with pytest.raises(chat.ChatProviderUnavailable):
        chat.answer_question(DOCUMENT_TEXT, QUESTION, {"GEMINI_API_KEY": ""})
    assert fake_genai.configured == fake_genai.models == fake_genai.requests == []


def test_json_formatter_serializes_diagnostic_extra(app, formatted_logs):
    fields = {
        "reason": "model_not_found",
        "provider_status": 404,
        "error_type": "FakeProviderError",
        "model": MODEL,
        "request_id": "formatter-test-request-id",
    }
    app.logger.warning("chat_provider_failed", extra={"diagnostic": fields})

    event = _event(_json_logs(formatted_logs), "chat_provider_failed", fields["request_id"])
    for name, value in fields.items():
        assert event[name] == value


def test_chat_success_has_unique_server_ids_and_safe_completion_logs(
    app, client, chat_documents, fake_genai, formatted_logs, caplog,
):
    app.config.update(GEMINI_API_KEY=API_KEY, GEMINI_MODEL=MODEL)
    request_ids = []
    for _ in range(2):
        response = _post_chat(client, chat_documents.token, chat_documents.owned)
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body["success"] is True
        assert body["data"] == {"answer": "Total: 42.", "document_id": chat_documents.owned}
        request_id = _request_id(response)
        request_ids.append(request_id)
        _completed(_json_logs(formatted_logs), request_id, 200)

    assert len(set(request_ids)) == 2
    assert len(fake_genai.requests) == 2
    for sent in fake_genai.requests:
        assert DOCUMENT_TEXT in sent["prompt"] and QUESTION in sent["prompt"]
        assert sent["options"]["http_options"].timeout == 20000
        assert sent["options"]["http_options"].retry_options.attempts == 1
    records = _json_logs(formatted_logs)
    assert not any(record.get("event", record.get("message")) == "chat_provider_failed" for record in records)
    _assert_redacted(formatted_logs.getvalue(), caplog.text, extra=(chat_documents.token,))


@pytest.mark.parametrize("error_type,status,reason,retryable,http_status", FAILURES)
def test_chat_provider_failure_is_safe_structured_and_correlated(
    error_type, status, reason, retryable, http_status,
    app, client, chat_documents, fake_genai, formatted_logs, caplog,
):
    app.config.update(GEMINI_API_KEY=API_KEY, GEMINI_MODEL=MODEL)
    fake_genai.error = _provider_error(error_type, status)

    response = _post_chat(client, chat_documents.token, chat_documents.owned)

    _assert_redacted(
        response.get_data(as_text=True), formatted_logs.getvalue(), caplog.text,
        extra=(chat_documents.token,),
    )
    assert response.status_code == http_status, response.get_json()
    body = response.get_json()
    assert body["success"] is False
    error = body["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"].strip()
    request_id = _request_id(response)
    details = error["details"]
    assert details["reason"] == reason
    assert details["request_id"] == request_id
    assert details["retryable"] is retryable

    records = _json_logs(formatted_logs)
    failed = _event(records, "chat_provider_failed", request_id)
    assert failed["reason"] == reason
    assert failed["provider_status"] == status
    assert failed["error_type"] == error_type.__name__
    assert failed["model"] == MODEL
    _completed(records, request_id, http_status)
    assert len(fake_genai.requests) == 1


def test_chat_without_key_returns_503_without_calling_provider(
    app, client, chat_documents, fake_genai, formatted_logs, caplog,
):
    app.config["GEMINI_API_KEY"] = ""
    response = _post_chat(client, chat_documents.token, chat_documents.owned)

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "provider_unavailable"
    assert fake_genai.configured == fake_genai.models == fake_genai.requests == []
    _completed(_json_logs(formatted_logs), _request_id(response), 503)
    _assert_redacted(response.get_data(as_text=True), formatted_logs.getvalue(), caplog.text,
                     extra=(chat_documents.token,))


@pytest.mark.parametrize(
    "document_name,status,code",
    [("empty", 422, "document_has_no_text"), ("foreign", 404, "not_found")],
    ids=["no-context", "cross-owner"],
)
def test_chat_rejects_missing_or_unowned_context_before_calling_provider(
    document_name, status, code, app, client, chat_documents, fake_genai, formatted_logs, caplog,
):
    app.config["GEMINI_API_KEY"] = API_KEY
    response = _post_chat(client, chat_documents.token, getattr(chat_documents, document_name))

    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code
    assert fake_genai.configured == fake_genai.models == fake_genai.requests == []
    _assert_redacted(response.get_data(as_text=True), formatted_logs.getvalue(), caplog.text,
                     extra=(chat_documents.token,))


def test_chat_requires_authentication_before_calling_provider(app, client, fake_genai, formatted_logs, caplog):
    app.config["GEMINI_API_KEY"] = API_KEY
    response = client.post("/api/v1/chat", json={"document_id": 1, "question": QUESTION})

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "authentication_required"
    assert fake_genai.configured == fake_genai.models == fake_genai.requests == []
    _assert_redacted(response.get_data(as_text=True), formatted_logs.getvalue(), caplog.text)


def test_formatter_ignores_non_allowlisted_diagnostic_fields(app, formatted_logs):
    app.logger.info("safe_diagnostic_event", extra={"diagnostic": {
        "request_id": "safe-id", "question": QUESTION, "api_key": API_KEY,
        "authorization": PROVIDER_TOKEN, "document_text": DOCUMENT_TEXT,
    }})
    record = _json_logs(formatted_logs)[0]
    assert record["request_id"] == "safe-id"
    assert "question" not in record and "api_key" not in record
    _assert_redacted(formatted_logs.getvalue())


def test_health_probes_keep_request_id_without_extra_completion_noise(client, formatted_logs):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
    assert "request_completed" not in formatted_logs.getvalue()


@pytest.mark.parametrize("status,reason,expected", [
    (HTTPStatus.NOT_FOUND, "model_not_found", 404),
    (lambda: SimpleNamespace(name="RESOURCE_EXHAUSTED"), "quota_exceeded", 429),
])
def test_provider_status_supports_http_enums_and_grpc_codes(status, reason, expected):
    result = chat._provider_failure(FakeProviderError(RAW_PROVIDER_MESSAGE, status))
    assert result.reason == reason
    assert result.provider_status == expected
    assert type(result.provider_status) is int


def test_sdk_import_runtime_failure_is_safe_and_diagnosable(
    app, client, chat_documents, formatted_logs, monkeypatch, caplog,
):
    app.config.update(GEMINI_API_KEY=API_KEY, GEMINI_MODEL=MODEL)
    original_import = builtins.__import__

    def incompatible_sdk(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "google" and "genai" in fromlist:
            raise TypeError(RAW_PROVIDER_MESSAGE)
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", incompatible_sdk)
    response = _post_chat(client, chat_documents.token, chat_documents.owned)

    assert response.status_code == 503
    assert response.get_json()["error"]["details"]["reason"] == "sdk_initialization_failed"
    failure = _event(_json_logs(formatted_logs), "chat_provider_failed", _request_id(response))
    assert failure["error_type"] == "TypeError"
    _assert_redacted(response.get_data(as_text=True), formatted_logs.getvalue(), caplog.text)
