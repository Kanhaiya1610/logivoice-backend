"""Optional Gemini document-grounded chat integration."""

from __future__ import annotations

import math
import re


class ChatProviderUnavailable(Exception):
    """Gemini has not been configured or its SDK is unavailable."""

    def __init__(self, reason: str = "not_configured", exception_type: str | None = None):
        self.reason = reason
        self.exception_type = exception_type
        super().__init__("Gemini chat is not configured or its SDK is unavailable")


class ChatProviderError(RuntimeError):
    """Safe diagnostic metadata, without the provider's possibly sensitive text."""

    def __init__(self, reason: str, provider_status: int | None, exception_type: str):
        self.reason = reason
        self.provider_status = provider_status
        self.exception_type = exception_type
        self.retryable = reason in {"quota_exceeded", "timeout", "service_unavailable"}
        super().__init__(f"Gemini request failed ({reason})")


def _provider_failure(error: Exception) -> ChatProviderError:
    # Google API exceptions have numeric `code`; gRPC errors may expose a
    # code() enum. Never log str(error), response payloads, or credential URLs.
    status = getattr(error, "code", None)
    if callable(status):
        try:
            status = status()
        except Exception:
            status = None
    grpc_codes = {
        "INVALID_ARGUMENT": 400, "UNAUTHENTICATED": 401, "PERMISSION_DENIED": 403,
        "NOT_FOUND": 404, "RESOURCE_EXHAUSTED": 429, "DEADLINE_EXCEEDED": 504,
        "UNAVAILABLE": 503,
    }
    if not isinstance(status, int) or isinstance(status, bool):
        status = grpc_codes.get(getattr(status, "name", ""))
    elif not 100 <= status <= 599:
        status = None
    else:
        status = int(status)
    reason = {
        400: "invalid_request", 401: "authentication_failed", 403: "permission_denied",
        404: "model_not_found", 429: "quota_exceeded", 503: "service_unavailable",
        504: "timeout",
    }.get(status, "provider_error")
    if isinstance(error, TimeoutError):
        reason = "timeout"
    exception_type = re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__)[:80]
    return ChatProviderError(reason, status, exception_type)


def _request_timeout(config: dict) -> float:
    try:
        timeout = float(config.get("GEMINI_TIMEOUT_SECONDS", 20))
    except (TypeError, ValueError):
        return 20
    return max(1, min(timeout, 25)) if math.isfinite(timeout) else 20


def answer_question(document_text: str, question: str, config: dict) -> str:
    api_key = config.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ChatProviderUnavailable("not_configured")
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise ChatProviderUnavailable("sdk_unavailable", type(error).__name__) from None
    except Exception as error:
        # An installed SDK can still fail to import due to a binary/runtime
        # incompatibility. Report the class, never its possibly private message.
        raise ChatProviderUnavailable("sdk_initialization_failed", type(error).__name__) from None

    prompt = (
        "Answer the user's question using only the document context below. "
        "If the answer is not present, say that the information is not found in the document. "
        "Do not invent values. Keep the answer concise.\n\n"
        f"Document context:\n{document_text[:12000]}\n\n"
        f"Question: {question[:2000]}"
    )
    client = None
    try:
        timeout_ms = int(_request_timeout(config) * 1000)
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        response = client.models.generate_content(
            model=config.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt,
        )
        answer = getattr(response, "text", "")
        if not isinstance(answer, str) or not answer.strip():
            raise ChatProviderError("empty_response", None, "EmptyResponse")
        return answer.strip()
    except ChatProviderError:
        raise
    except Exception as error:
        raise _provider_failure(error) from None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
