from __future__ import annotations

import sys
from types import ModuleType

from app.services import processing


def test_pdf_text_uses_fast_pymupdf_path_before_pdfplumber(monkeypatch):
    class Page:
        def get_text(self):
            return "Fast PDF text"

    class Document:
        def __iter__(self):
            return iter([Page()])

        def close(self):
            pass

    fitz = ModuleType("fitz")
    fitz.open = lambda **kwargs: Document()
    pdfplumber = ModuleType("pdfplumber")
    pdfplumber.open = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("pdfplumber should not be used when PyMuPDF is available")
    )
    monkeypatch.setitem(sys.modules, "fitz", fitz)
    monkeypatch.setitem(sys.modules, "pdfplumber", pdfplumber)

    assert processing.extract_text(b"pdf-bytes", ".pdf") == "Fast PDF text"


def test_pdf_text_respects_page_limit(monkeypatch):
    class Page:
        def __init__(self, number):
            self.number = number

        def get_text(self):
            return f"Page {self.number}"

    class Document:
        def __iter__(self):
            return iter([Page(1), Page(2), Page(3)])

        def close(self):
            pass

    fitz = ModuleType("fitz")
    fitz.open = lambda **kwargs: Document()
    monkeypatch.setitem(sys.modules, "fitz", fitz)
    monkeypatch.setenv("MAX_PDF_PAGES", "2")

    assert processing.extract_text(b"pdf-bytes", ".pdf") == "Page 1\nPage 2"


def test_process_content_adds_entities_and_image_barcodes(monkeypatch):
    monkeypatch.setattr(
        processing,
        "extract_text",
        lambda content, suffix: "Resume\nProfessional Experience\nEducation\nSkills",
    )
    monkeypatch.setattr(
        processing,
        "extract_entities",
        lambda text: {
            "people": ["Kanhaiya Lal"],
            "organizations": ["Okinawa Institute of Science and Technology"],
            "locations": [],
            "dates": [],
            "emails": [],
            "phones": [],
            "urls": [],
            "source": "regex",
        },
    )
    monkeypatch.setattr(
        processing,
        "decode_image",
        lambda content: [{"type": "QRCODE", "data": "https://example.test/status"}],
    )

    result = processing.process_content(b"image-bytes", ".png", filename="candidate_resume.png")

    assert result["classification"] == "Resume/CV"
    assert result["extracted_fields"]["entities"]["people"] == ["Kanhaiya Lal"]
    assert result["extracted_fields"]["barcodes"] == [
        {"type": "QRCODE", "data": "https://example.test/status"}
    ]


def test_process_content_adds_page_aware_pdf_barcodes(monkeypatch):
    monkeypatch.setattr(processing, "extract_text", lambda content, suffix: "Delivery Note\nReceived by: Alex")
    monkeypatch.setattr(processing, "extract_entities", lambda text: {"source": "regex"})
    monkeypatch.setattr(
        processing,
        "decode_pdf",
        lambda content: [{"type": "CODE128", "data": "SHIP-42", "page": 2}],
    )

    result = processing.process_content(b"pdf-bytes", ".pdf", filename="delivery-note.pdf")

    assert result["classification"] == "Delivery Note"
    assert result["extracted_fields"]["barcodes"] == [
        {"type": "CODE128", "data": "SHIP-42", "page": 2}
    ]


def test_split_logical_records_requires_distinct_invoice_numbers():
    text = (
        "Invoice No.: 1234\nCustomer Name: John Doe\nDelivery Address: Main St\n\n"
        "Invoice No.: 5678\nCustomer Name: Jane Smith\nDelivery Address: Elm St"
    )

    records = processing.split_logical_records(text)

    assert len(records) == 2
    assert "Invoice No.: 1234" in records[0]
    assert "Customer Name: Jane Smith" in records[1]


def test_split_logical_records_keeps_repeated_page_headers_together():
    text = "Invoice No.: 1234\nPage 1\n\nInvoice No.: 1234\nPage 2"

    assert processing.split_logical_records(text) == [text]


def test_extract_fields_supports_customer_name_label():
    fields = processing.extract_fields(
        "Invoice No.: 1234\nCustomer Name: John Doe\nDelivery Address: Main St"
    )

    assert fields["invoice_number"] == "1234"
    assert fields["customer_name"] == "John Doe"
    assert fields["address"] == "Main St"
