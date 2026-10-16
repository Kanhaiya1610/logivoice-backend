"""Versioned LogiVoice REST API."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePath
from uuid import uuid4

from flask import Blueprint, current_app, g, request, send_file
from sqlalchemy import cast, func, or_, text
from werkzeug.utils import secure_filename

from ..auth import auth_required, create_access_token
from ..errors import APIError
from ..extensions import db
from ..models import Document, User
from ..responses import success_response
from ..services.chat import ChatProviderError, ChatProviderUnavailable, answer_question
from ..diagnostics import safe_model_name
from ..services.processing import (
    EXTENSION_MIME,
    ProcessingError,
    process_content,
    processed_timestamp,
    validate_and_read,
)
from ..services.storage import StorageError, StorageNotFound, get_storage
from ..rate_limit import rate_limit


api_v1 = Blueprint("api_v1", __name__)


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise APIError("invalid_json", "The JSON request body must be an object.", 400)
    return body


def _pagination(page_value, per_page_value) -> tuple[int, int]:
    try:
        page = int(page_value)
        per_page = int(per_page_value)
    except (TypeError, ValueError) as error:
        raise APIError("invalid_pagination", "page and per_page must be integers.", 400) from error
    if page < 1 or per_page < 1 or per_page > 100:
        raise APIError("invalid_pagination", "page must be >= 1 and per_page must be between 1 and 100.", 400)
    return page, per_page


def _pagination_meta(page: int, per_page: int, total: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": math.ceil(total / per_page) if total else 0,
    }


def _owned_document(document_id: int) -> Document:
    document = db.session.get(Document, document_id)
    if document is None or document.owner_id != g.current_user.id:
        # Do not disclose whether another user's ID exists.
        raise APIError("not_found", "Document not found.", 404)
    return document


def _document_query(q: str | None = None, status: str | None = None):
    query = Document.query.filter(Document.owner_id == g.current_user.id)
    if status:
        query = query.filter(Document.status == status)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Document.original_filename.ilike(term),
                Document.extracted_text.ilike(term),
                Document.classification.ilike(term),
                cast(Document.extracted_fields, db.String).ilike(term),
            )
        )
    return query


def _validate_email(email: object) -> str:
    if not isinstance(email, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
        raise APIError("invalid_email", "A valid email address is required.", 400)
    return email.strip().lower()


@api_v1.get("/health")
def health():
    database = "ok"
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        current_app.logger.exception("Health check database probe failed")
        database = "unavailable"
    if database != "ok":
        return success_response(
            {"status": "degraded", "database": database},
            message="The API is running but the database is unavailable.",
            status=503,
        )
    return success_response(
        {"status": "ok", "database": database},
        message="LogiVoice API is healthy.",
    )


@api_v1.post("/auth/register")
@rate_limit("auth-register", 10, 60)
def register():
    body = _json_body()
    email = _validate_email(body.get("email"))
    password = body.get("password")
    if not isinstance(password, str) or len(password) < 8:
        raise APIError("invalid_password", "Password must contain at least 8 characters.", 400)
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 120):
        raise APIError("invalid_name", "name must be a string of at most 120 characters.", 400)
    if db.session.scalar(db.select(User).where(User.email == email)) is not None:
        raise APIError("email_in_use", "An account with that email already exists.", 409)

    user = User(email=email, name=name.strip() if isinstance(name, str) else None)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return success_response(
        {"user": user.to_dict(), "access_token": create_access_token(user)},
        message="Registration successful.",
        status=201,
    )


@api_v1.post("/auth/login")
@rate_limit("auth-login", 10, 60)
def login():
    body = _json_body()
    email = _validate_email(body.get("email"))
    password = body.get("password")
    user = db.session.scalar(db.select(User).where(User.email == email))
    if user is None or not isinstance(password, str) or not user.check_password(password):
        raise APIError("invalid_credentials", "Email or password is incorrect.", 401)
    return success_response(
        {"user": user.to_dict(), "access_token": create_access_token(user)},
        message="Login successful.",
    )


@api_v1.post("/auth/logout")
@auth_required
def logout():
    # Incrementing the version invalidates all currently issued tokens for this
    # user. New login is required; no token or document content is logged.
    g.current_user.token_version += 1
    db.session.commit()
    return success_response(message="Logout successful.")


@api_v1.get("/auth/me")
@auth_required
def me():
    return success_response({"user": g.current_user.to_dict()})


@api_v1.post("/documents")
@auth_required
@rate_limit("document-upload", 30, 3600)
def upload_documents():
    uploads = request.files.getlist("files") + request.files.getlist("pdfFile")
    uploads = [upload for upload in uploads if upload.filename]
    if not uploads:
        raise APIError("files_required", "Upload one or more files using the files field.", 400)

    max_bytes = current_app.config.get(
        "MAX_UPLOAD_SIZE_BYTES",
        current_app.config.get("MAX_CONTENT_LENGTH", 25 * 1024 * 1024),
    )
    prepared = []
    for upload in uploads:
        try:
            safe_name, mime_type, content = validate_and_read(upload, max_bytes)
        except OverflowError as error:
            raise APIError("file_too_large", str(error), 413) from error
        except (ValueError, ProcessingError) as error:
            raise APIError("invalid_upload", str(error), 400) from error
        prepared.append((safe_name, mime_type, content))

    storage = get_storage()
    saved_keys = []
    documents = []
    try:
        for safe_name, mime_type, content in prepared:
            suffix = PurePath(safe_name).suffix.lower()
            key = f"documents/{g.current_user.id}/{uuid4().hex}{suffix}"
            storage.save(BytesIO(content), key, mime_type)
            saved_keys.append(key)
            document = Document(
                owner_id=g.current_user.id,
                original_filename=secure_filename(safe_name),
                storage_key=key,
                mime_type=mime_type,
                file_size=len(content),
                status="processing",
            )
            try:
                result = process_content(content, suffix, filename=safe_name)
                document.extracted_text = result["extracted_text"]
                document.extracted_fields = result["extracted_fields"]
                document.classification = result["classification"]
                document.classification_confidence = result["classification_confidence"]
                document.status = "processed"
                document.status_message = (
                    None if result["extracted_text"].strip() else "No text was extracted from the document."
                )
            except ProcessingError as error:
                document.status = "failed"
                document.status_message = str(error)[:500]
                current_app.logger.warning("Document processing failed for %s: %s", safe_name, error)
            document.processed_at = processed_timestamp()
            db.session.add(document)
            documents.append(document)
        db.session.commit()
    except StorageError as error:
        db.session.rollback()
        for key in saved_keys:
            try:
                storage.delete(key)
            except StorageError:
                current_app.logger.exception("Could not clean up storage key after upload failure")
        raise APIError("storage_unavailable", "The document could not be stored.", 503) from error
    except Exception as error:
        db.session.rollback()
        for key in saved_keys:
            try:
                storage.delete(key)
            except StorageError:
                current_app.logger.exception("Could not clean up storage key after database failure")
        current_app.logger.exception("Document persistence failed")
        raise APIError("persistence_error", "The document could not be saved.", 500) from error

    return success_response(
        {"documents": [document.to_dict(include_text=True) for document in documents]},
        message="Document upload completed.",
        status=201,
    )


@api_v1.get("/documents")
@auth_required
def list_documents():
    page, per_page = _pagination(request.args.get("page", 1), request.args.get("per_page", 20))
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip().lower()
    if status and status not in {"processing", "processed", "failed"}:
        raise APIError("invalid_status", "status must be processing, processed, or failed.", 400)
    query = _document_query(q, status)
    total = query.order_by(None).count()
    records = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return success_response(
        {"documents": [document.to_dict() for document in records]},
        meta=_pagination_meta(page, per_page, total),
    )


@api_v1.get("/documents/<int:document_id>")
@auth_required
def get_document(document_id: int):
    document = _owned_document(document_id)
    return success_response({"document": document.to_dict(include_text=True)})


@api_v1.delete("/documents/<int:document_id>")
@auth_required
def delete_document(document_id: int):
    document = _owned_document(document_id)
    storage = get_storage()
    try:
        storage.delete(document.storage_key)
    except StorageError as error:
        current_app.logger.warning("Storage deletion failed for document %s", document.id)
        raise APIError("storage_unavailable", "The document file could not be deleted.", 503) from error
    db.session.delete(document)
    db.session.commit()
    return success_response(message="Document deleted.")


@api_v1.get("/documents/<int:document_id>/download")
@auth_required
def download_document(document_id: int):
    document = _owned_document(document_id)
    try:
        stream = get_storage().open(document.storage_key)
    except StorageNotFound as error:
        raise APIError("file_not_found", "The document file is no longer available.", 404) from error
    except StorageError as error:
        raise APIError("storage_unavailable", "The document file could not be read.", 503) from error
    return send_file(
        stream,
        mimetype=document.mime_type,
        as_attachment=True,
        download_name=document.original_filename,
        max_age=0,
    )


@api_v1.post("/search")
@auth_required
@rate_limit("document-search", 120, 3600)
def search_documents():
    body = _json_body()
    q = body.get("q", body.get("query", ""))
    if not isinstance(q, str):
        raise APIError("invalid_query", "q must be a string.", 400)
    if len(q) > 300:
        raise APIError("invalid_query", "q must contain at most 300 characters.", 400)
    status = body.get("status", "")
    if status and status not in {"processing", "processed", "failed"}:
        raise APIError("invalid_status", "status must be processing, processed, or failed.", 400)
    page, per_page = _pagination(body.get("page", 1), body.get("per_page", 20))
    query = _document_query(q.strip(), status)
    total = query.order_by(None).count()
    records = (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return success_response(
        {"documents": [document.to_dict() for document in records]},
        meta=_pagination_meta(page, per_page, total),
    )


@api_v1.post("/chat")
@auth_required
@rate_limit("document-chat", 30, 3600)
def chat():
    body = _json_body()
    try:
        document_id = int(body.get("document_id"))
    except (TypeError, ValueError) as error:
        raise APIError("document_required", "document_id is required.", 400) from error
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise APIError("question_required", "question is required.", 400)
    document = _owned_document(document_id)
    if not document.extracted_text.strip():
        raise APIError("document_has_no_text", "This document has no extracted text to use as context.", 422)
    try:
        answer = answer_question(document.extracted_text, question.strip(), current_app.config)
    except ChatProviderUnavailable as error:
        current_app.logger.warning("chat_provider_failed", extra={"diagnostic": {
            "provider": "gemini", "model": safe_model_name(current_app.config),
            "reason": error.reason, "provider_status": None,
            "error_type": error.exception_type or type(error).__name__, "retryable": False,
        }})
        raise APIError("provider_unavailable", "Gemini chat is not configured or unavailable.", 503,
                       {"reason": error.reason, "request_id": g.request_id, "retryable": False}) from None
    except ChatProviderError as error:
        current_app.logger.warning("chat_provider_failed", extra={"diagnostic": {
            "provider": "gemini", "model": safe_model_name(current_app.config),
            "reason": error.reason, "provider_status": error.provider_status,
            "error_type": error.exception_type, "retryable": error.retryable,
        }})
        message, status = {
            "model_not_found": ("The configured chat model is unavailable. Contact the administrator.", 502),
            "invalid_request": ("The chat provider rejected the request. Contact the administrator.", 502),
            "authentication_failed": ("Chat provider authentication failed. Contact the administrator.", 502),
            "permission_denied": ("The chat provider denied access. Contact the administrator.", 502),
            "quota_exceeded": ("Chat quota or rate limit reached. Please try again later.", 503),
            "service_unavailable": ("The chat provider is temporarily unavailable. Please try again later.", 503),
            "timeout": ("The chat provider timed out. Please try again.", 504),
            "empty_response": ("The chat provider returned no usable answer. Try rephrasing the question.", 502),
        }.get(error.reason, ("The chat provider could not answer the request.", 502))
        raise APIError("provider_error", message, status,
                       {"reason": error.reason, "request_id": g.request_id,
                        "retryable": error.retryable}) from None
    return success_response({"answer": answer, "document_id": document.id})


@api_v1.get("/analytics/overview")
@auth_required
def analytics_overview():
    base = Document.query.filter(Document.owner_id == g.current_user.id)
    total = base.count()
    status_rows = (
        db.session.query(Document.status, func.count(Document.id))
        .filter(Document.owner_id == g.current_user.id)
        .group_by(Document.status)
        .all()
    )
    classification_rows = (
        db.session.query(Document.classification, func.count(Document.id))
        .filter(Document.owner_id == g.current_user.id)
        .group_by(Document.classification)
        .all()
    )
    by_status = {status: count for status, count in status_rows}
    by_classification = {classification: count for classification, count in classification_rows}
    return success_response(
        {
            "total_documents": total,
            "processed_documents": by_status.get("processed", 0),
            "processing_documents": by_status.get("processing", 0),
            "failed_documents": by_status.get("failed", 0),
            "by_status": by_status,
            "status_counts": by_status,
            "by_classification": by_classification,
            "document_types": by_classification,
        }
    )
