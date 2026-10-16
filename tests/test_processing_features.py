from __future__ import annotations

from app.services import processing


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
