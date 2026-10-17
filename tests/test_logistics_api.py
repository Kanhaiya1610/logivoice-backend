from pathlib import Path

from app.extensions import db
from app.models import Document

from .conftest import headers, register, txt_file


INVOICE = (Path(__file__).parent / "fixtures" / "invoice_with_logistics.txt").read_text(encoding="utf-8")


def upload(client, token, text=INVOICE):
    response = client.post(
        "/api/v1/documents",
        data=txt_file(name="logistics.txt", content=text),
        headers=headers(token),
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["data"]["documents"]


def test_logistics_fields_persist_and_search_remains_owned(client, app):
    token = register(client)
    other = register(client, "other@example.test")
    document = upload(client, token)[0]
    fields = document["fields"]
    assert {key: fields[key] for key in (
        "subtotal", "tax", "total", "currency", "due_date",
        "purchase_order_number", "tracking_number", "carrier",
    )} == {
        "subtotal": "125.50", "tax": "12.55", "total": "138.05", "currency": "USD",
        "due_date": "October 31, 2026", "purchase_order_number": "000042",
        "tracking_number": "001234567890", "carrier": "Example Carrier",
    }
    assert fields["line_items"] == [
        {"description": "Widget A", "sku": "0007", "quantity": "2", "unit": "pcs",
         "unit_price": "50.00", "amount": "100.00"},
        {"description": "Widget B", "sku": "SKU-0008", "quantity": "3", "unit": "pcs",
         "unit_price": "8.50", "amount": "25.50"},
    ]
    # Read through a fresh request as well as the underlying JSON column.
    fetched = client.get(f"/api/v1/documents/{document['id']}", headers=headers(token))
    assert fetched.get_json()["data"]["document"]["fields"] == fields
    with app.app_context():
        assert db.session.get(Document, document["id"]).extracted_fields == fields

    for query in ("SKU-0008", "001234567890", "000042"):
        results = client.get("/api/v1/documents", query_string={"q": query}, headers=headers(token))
        assert [item["id"] for item in results.get_json()["data"]["documents"]] == [document["id"]]
        hidden = client.get("/api/v1/documents", query_string={"q": query}, headers=headers(other))
        assert hidden.get_json()["data"]["documents"] == []
    assert client.get(f"/api/v1/documents/{document['id']}", headers=headers(other)).status_code == 404
    assert client.post(f"/api/v1/documents/{document['id']}/reprocess", headers=headers(other)).status_code == 404


def test_legacy_document_reprocessing_adds_fields_without_changing_original(client, app):
    token = register(client)
    document = upload(client, token)[0]
    with app.app_context():
        stored = db.session.get(Document, document["id"])
        stored.extracted_fields = {"invoice_number": "INV-LOG-001"}
        original_key = stored.storage_key
        db.session.commit()

    result = client.post(f"/api/v1/documents/{document['id']}/reprocess", headers=headers(token))
    assert result.status_code == 200, result.get_json()
    refreshed = result.get_json()["data"]["document"]
    assert refreshed["id"] == document["id"]
    assert refreshed["fields"] == document["fields"]
    download = client.get(f"/api/v1/documents/{document['id']}/download", headers=headers(token))
    assert download.data.decode("utf-8") == INVOICE
    with app.app_context():
        assert db.session.get(Document, document["id"]).storage_key == original_key
        assert db.session.query(Document).count() == 1


def test_split_invoice_metadata_remains_local_after_reprocessing(client):
    token = register(client)
    text = (
        "Invoice Number: INV-A001\nCustomer Name: Example A\n"
        "Items:\nWidget A 10 pcs\nSubtotal: 20.00\nTax: 0.00\n"
        "Total: USD 20.00\nTracking Number: 000111\n\n"
        "Invoice Number: INV-B002\nCustomer Name: Example B\n"
        "Items:\nWidget B 3 boxes\nTotal: EUR 30.50\nPO Number: 000222\n"
    )
    first, second = upload(client, token, text)
    assert first["fields"]["total"] == "20.00"
    assert first["fields"]["tax"] == "0.00"
    assert first["fields"]["currency"] == "USD"
    assert first["fields"]["purchase_order_number"] is None
    assert second["fields"]["total"] == "30.50"
    assert second["fields"]["currency"] == "EUR"
    assert second["fields"]["tax"] is None
    assert second["fields"]["tracking_number"] is None
    assert [item["description"] for item in first["fields"]["line_items"]] == ["Widget A"]
    assert [item["description"] for item in second["fields"]["line_items"]] == ["Widget B"]

    for document in (first, second):
        result = client.post(f"/api/v1/documents/{document['id']}/reprocess", headers=headers(token))
        assert result.status_code == 200, result.get_json()
        refreshed = result.get_json()["data"]["document"]
        assert refreshed["fields"] == document["fields"]
        assert refreshed["record_count"] == 2
    search = client.get("/api/v1/documents", query_string={"q": "000222"}, headers=headers(token))
    assert [item["id"] for item in search.get_json()["data"]["documents"]] == [second["id"]]


def test_unstructured_upload_does_not_invent_financial_or_shipment_data(client):
    token = register(client)
    document = upload(client, token, "Meeting notes\nDiscuss widgets and delivery options next week.")[0]
    assert document["status"] == "processed"
    assert document["fields"]["line_items"] == []
    for field in ("subtotal", "tax", "total", "currency", "due_date",
                  "purchase_order_number", "tracking_number", "carrier"):
        assert document["fields"][field] is None
