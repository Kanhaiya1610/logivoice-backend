from __future__ import annotations

import io

import pytest

from app import create_app
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "APP_ENV": "testing",
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'test.sqlite').as_posix()}",
            "STORAGE_BACKEND": "local",
            "STORAGE_ROOT": str(tmp_path / "storage"),
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "MAX_UPLOAD_SIZE_BYTES": 1024 * 1024,
            "GEMINI_API_KEY": "",
            "CORS_ORIGINS": ["http://localhost:5173"],
        }
    )
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email="user@example.com", password="correct horse battery staple"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "Test User"},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["access_token"]


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def txt_file(name="invoice.txt", content="Invoice Number: INV-123\nTotal: 42\n"):
    return {"files": (io.BytesIO(content.encode("utf-8")), name)}
