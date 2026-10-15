"""Database models."""

from __future__ import annotations

from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    token_version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    documents = db.relationship(
        "Document",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Document(db.Model):
    __tablename__ = "documents"
    __table_args__ = (
        db.Index("ix_documents_owner_status", "owner_id", "status"),
        db.Index("ix_documents_owner_created_at", "owner_id", "created_at"),
        db.Index("ix_documents_owner_classification", "owner_id", "classification"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename = db.Column(db.String(255), nullable=False)
    storage_key = db.Column(db.String(512), nullable=False, unique=True)
    mime_type = db.Column(db.String(100), nullable=False)
    file_size = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="processing")
    status_message = db.Column(db.String(500), nullable=True)
    extracted_text = db.Column(db.Text, nullable=False, default="")
    extracted_fields = db.Column(db.JSON, nullable=False, default=dict)
    classification = db.Column(db.String(80), nullable=False, default="Unknown")
    classification_confidence = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    owner = db.relationship("User", back_populates="documents")

    def to_dict(self, include_text: bool = False) -> dict:
        result = {
            "id": self.id,
            "filename": self.original_filename,
            "mime_type": self.mime_type,
            "file_size": self.file_size,
            "status": self.status,
            "status_message": self.status_message,
            "classification": self.classification,
            "classification_confidence": round(self.classification_confidence or 0.0, 4),
            "fields": self.extracted_fields or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }
        if include_text:
            result["extracted_text"] = self.extracted_text or ""
        return result
