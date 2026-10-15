"""Application errors and centralized HTTP error handling."""

from __future__ import annotations

from flask import Flask, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .responses import error_response


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(APIError)
    def handle_api_error(error: APIError):
        return error_response(error.code, error.message, error.status, error.details)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(_error):
        return error_response(
            "payload_too_large",
            "The upload exceeds the configured request size limit.",
            413,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        status = error.code or 500
        code = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
        }.get(status, "http_error")
        message = error.description if status < 500 else "The request could not be completed."
        return error_response(code, message, status)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        app.logger.exception("Unhandled request error", exc_info=error)
        return error_response("internal_error", "An internal server error occurred.", 500)

    @app.before_request
    def reject_oversized_content_length():
        # Flask handles MAX_CONTENT_LENGTH before the view, but this keeps the
        # behavior explicit for servers that omit Content-Length.
        max_size = app.config.get("MAX_CONTENT_LENGTH")
        if max_size and request.content_length and request.content_length > max_size:
            raise RequestEntityTooLarge()
