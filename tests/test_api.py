from __future__ import annotations

import io

from .conftest import headers, register, txt_file


def test_health_is_public_and_structured(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"


def test_registration_login_me_and_ownership(client):
    owner_token = register(client, "owner@example.com")
    other_token = register(client, "other@example.com")

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    login_token = login.get_json()["data"]["access_token"]
    assert client.get("/api/v1/auth/me", headers=headers(login_token)).status_code == 200

    uploaded = client.post(
        "/api/v1/documents",
        data=txt_file(),
        headers=headers(owner_token),
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201, uploaded.get_json()
    document_id = uploaded.get_json()["data"]["documents"][0]["id"]

    forbidden_view = client.get(f"/api/v1/documents/{document_id}", headers=headers(other_token))
    assert forbidden_view.status_code == 404
    forbidden_delete = client.delete(f"/api/v1/documents/{document_id}", headers=headers(other_token))
    assert forbidden_delete.status_code == 404


def test_txt_upload_extracts_text_fields_and_classification(client):
    token = register(client)
    response = client.post(
        "/api/v1/documents",
        data=txt_file(
            content=(
                "Invoice Number: INV-2024-7\n"
                "Customer: Northwind Ltd\n"
                "Delivery Address: Rotterdam\n"
                "Invoice Date: 2024-05-10\n"
                "Total: 100\n"
            )
        ),
        headers=headers(token),
        content_type="multipart/form-data",
    )

    assert response.status_code == 201, response.get_json()
    document = response.get_json()["data"]["documents"][0]
    assert document["status"] == "processed"
    assert document["classification"] == "Invoice"
    assert document["fields"]["invoice_number"] == "INV-2024-7"
    assert document["fields"]["customer_name"] == "Northwind Ltd"
    assert "Northwind" in document["extracted_text"]


def test_search_and_pagination_are_scoped_to_owner(client):
    token = register(client)
    files = {
        "files": [
            (io.BytesIO(b"Invoice Number: ALPHA-1\nTotal: 1"), "alpha-one.txt"),
            (io.BytesIO(b"Invoice Number: ALPHA-2\nTotal: 2"), "alpha-two.txt"),
            (io.BytesIO(b"Invoice Number: ALPHA-3\nTotal: 3"), "alpha-three.txt"),
        ]
    }
    uploaded = client.post(
        "/api/v1/documents",
        data=files,
        headers=headers(token),
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201, uploaded.get_json()

    page_one = client.post(
        "/api/v1/search",
        json={"q": "ALPHA", "page": 1, "per_page": 2},
        headers=headers(token),
    )
    page_two = client.post(
        "/api/v1/search",
        json={"q": "ALPHA", "page": 2, "per_page": 2},
        headers=headers(token),
    )
    assert page_one.status_code == 200
    assert len(page_one.get_json()["data"]["documents"]) == 2
    assert len(page_two.get_json()["data"]["documents"]) == 1
    assert page_one.get_json()["meta"] == {"page": 1, "per_page": 2, "total": 3, "pages": 2}


def test_invalid_upload_returns_useful_validation_error(client):
    token = register(client)
    response = client.post(
        "/api/v1/documents",
        data={"files": (io.BytesIO(b"not a pdf"), "bad.pdf")},
        headers=headers(token),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "invalid_upload"


def test_chat_reports_unavailable_provider(client):
    token = register(client)
    uploaded = client.post(
        "/api/v1/documents",
        data=txt_file(),
        headers=headers(token),
        content_type="multipart/form-data",
    )
    document_id = uploaded.get_json()["data"]["documents"][0]["id"]

    response = client.post(
        "/api/v1/chat",
        json={"document_id": document_id, "question": "What is the invoice number?"},
        headers=headers(token),
    )

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "provider_unavailable"


def test_analytics_and_authenticated_download_return_stored_document_data(client):
    token = register(client)
    uploaded = client.post(
        "/api/v1/documents",
        data=txt_file(name="download-me.txt", content="Invoice Number: DOWNLOAD-1\nTotal: 9"),
        headers=headers(token),
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201, uploaded.get_json()
    document = uploaded.get_json()["data"]["documents"][0]

    analytics = client.get("/api/v1/analytics/overview", headers=headers(token))
    assert analytics.status_code == 200
    analytics_data = analytics.get_json()["data"]
    assert analytics_data["total_documents"] == 1
    assert analytics_data["processed_documents"] == 1
    assert analytics_data["document_types"]["Invoice"] == 1

    download = client.get(
        f"/api/v1/documents/{document['id']}/download",
        headers=headers(token),
    )
    assert download.status_code == 200
    assert download.data == b"Invoice Number: DOWNLOAD-1\nTotal: 9"
