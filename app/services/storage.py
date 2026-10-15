"""Binary storage abstraction for local development and S3-compatible storage."""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from typing import BinaryIO

from flask import current_app


class StorageError(Exception):
    """A storage operation failed."""


class StorageNotFound(StorageError):
    """The requested object does not exist."""


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise StorageError("Invalid storage key")
        return candidate

    def save(self, stream: BinaryIO, key: str, content_type: str) -> None:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            os.replace(temporary, destination)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise StorageError("Unable to save the uploaded document") from error

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return path.open("rb")
        except FileNotFoundError as error:
            raise StorageNotFound("Document file not found") from error
        except OSError as error:
            raise StorageError("Unable to read the document") from error

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as error:
            raise StorageError("Unable to delete the document file") from error


class S3Storage:
    def __init__(self, app):
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError as error:  # pragma: no cover - dependency is bounded in requirements
            raise RuntimeError("STORAGE_BACKEND=s3 requires boto3") from error

        self._client_error = ClientError
        self.bucket = app.config["S3_BUCKET"]
        if not self.bucket:
            raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        kwargs = {}
        if app.config.get("S3_REGION"):
            kwargs["region_name"] = app.config["S3_REGION"]
        if app.config.get("S3_ENDPOINT_URL"):
            kwargs["endpoint_url"] = app.config["S3_ENDPOINT_URL"]
        if app.config.get("S3_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"] = app.config["S3_ACCESS_KEY_ID"]
            kwargs["aws_secret_access_key"] = app.config.get("S3_SECRET_ACCESS_KEY", "")
        self.client = boto3.client("s3", **kwargs)

    def save(self, stream: BinaryIO, key: str, content_type: str) -> None:
        try:
            self.client.upload_fileobj(
                stream,
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception as error:
            raise StorageError("Unable to save the uploaded document") from error

    def open(self, key: str) -> BinaryIO:
        output = io.BytesIO()
        try:
            self.client.download_fileobj(self.bucket, key, output)
        except self._client_error as error:
            code = error.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise StorageNotFound("Document file not found") from error
            raise StorageError("Unable to read the document") from error
        except Exception as error:
            raise StorageError("Unable to read the document") from error
        output.seek(0)
        return output

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            raise StorageError("Unable to delete the document file") from error


def build_storage(app):
    backend = app.config.get("STORAGE_BACKEND", "local").lower()
    if backend == "local":
        return LocalStorage(app.config["STORAGE_ROOT"])
    if backend == "s3":
        return S3Storage(app)
    raise RuntimeError(f"Unsupported STORAGE_BACKEND: {backend}")


def get_storage():
    return current_app.extensions["storage"]
