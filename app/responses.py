"""Consistent JSON response helpers."""

from __future__ import annotations

from flask import jsonify


def success_response(data=None, message: str | None = None, status: int = 200, meta=None):
    body = {"success": True, "data": data}
    if message:
        body["message"] = message
    if meta is not None:
        body["meta"] = meta
    return jsonify(body), status


def error_response(code: str, message: str, status: int, details=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status
