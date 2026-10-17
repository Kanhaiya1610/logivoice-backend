"""Isolated contracts for the optional document NLP service."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import nlp as nlp_service


def test_regex_fallback_extracts_logistics_values_and_deduplicates(monkeypatch):
    monkeypatch.setattr(nlp_service, "load_nlp", lambda *args, **kwargs: None)
    text = """
    Company: Acme Logistics
    Customer: Acme Logistics
    City: Rotterdam
    Port: Rotterdam
    Country: Netherlands
    Date: 2025-01-31; backup date January 31, 2025
    Contact: dispatch@example.test and DISPATCH@example.test
    Phone: +1 (555) 123-4567; repeat +1 (555) 123-4567
    Portal: https://example.test/shipments/42, again https://example.test/shipments/42.
    """

    entities = nlp_service.extract_entities(text)

    assert set(entities) == {
        "people",
        "organizations",
        "locations",
        "dates",
        "emails",
        "phones",
        "urls",
        "source",
    }
    assert entities["source"] == "regex"
    assert entities["organizations"] == ["Acme Logistics"]
    assert entities["locations"] == ["Rotterdam", "Netherlands"]
    assert entities["dates"] == ["2025-01-31", "January 31, 2025"]
    assert entities["emails"] == ["dispatch@example.test"]
    assert entities["phones"] == ["+1 (555) 123-4567"]
    assert entities["urls"] == ["https://example.test/shipments/42"]
    assert entities["people"] == []


def test_spacy_entities_use_supported_labels_and_preserve_first_occurrence():
    class FakeEntity:
        def __init__(self, value, label):
            self.text = value
            self.label_ = label

    class FakeNLP:
        def __call__(self, text):
            assert text.startswith("Document")
            return SimpleNamespace(
                ents=[
                    FakeEntity("Jane Doe", "PERSON"),
                    FakeEntity("Acme Logistics", "ORG"),
                    FakeEntity("Jane Doe", "PERSON"),
                    FakeEntity("Rotterdam", "GPE"),
                    FakeEntity("Rotterdam", "LOC"),
                    FakeEntity("2025-01-31", "DATE"),
                    FakeEntity("2025-01-31", "DATE"),
                    FakeEntity("ignored money", "MONEY"),
                ]
            )

    entities = nlp_service.extract_entities("Document text", nlp=FakeNLP())

    assert entities == {
        "people": ["Jane Doe"],
        "organizations": ["Acme Logistics"],
        "locations": ["Rotterdam"],
        "dates": ["2025-01-31"],
        "emails": [],
        "phones": [],
        "urls": [],
        "source": "spacy",
    }


def test_spacy_entities_are_enriched_with_contact_regex_values():
    class FakeNLP:
        def __call__(self, _text):
            return SimpleNamespace(ents=[SimpleNamespace(text="Acme Logistics", label_="ORG")])

    entities = nlp_service.extract_entities(
        "Company: Acme Logistics\nContact: ops@example.test\nPhone: +1 555 123 4567",
        nlp=FakeNLP(),
    )

    assert entities["organizations"] == ["Acme Logistics"]
    assert entities["emails"] == ["ops@example.test"]
    assert entities["phones"] == ["+1 555 123 4567"]
    assert entities["source"] == "spacy+regex"


def test_classification_is_content_first_even_when_filename_disagrees():
    label, confidence = nlp_service.classify_document(
        "INVOICE\nInvoice Number: INV-42\nAmount Due: 125.00\nPayment Terms: Net 30",
        filename="candidate_resume.pdf",
    )

    assert label == "Invoice"
    assert 0.5 < confidence <= 0.99


def test_classification_recognizes_cv_content():
    label, confidence = nlp_service.classify_document(
        "CURRICULUM VITAE\nProfessional Experience\nEducation\nSkills",
        filename="document.txt",
    )

    assert label == "Resume/CV"
    assert confidence > 0.5


def test_weak_content_and_filename_hint_return_unknown():
    label, confidence = nlp_service.classify_document(
        "A generic document with notes and a total.",
        filename="invoice.pdf",
    )

    assert label == "Unknown"
    assert 0.0 <= confidence < 0.5


def test_qr_reference_content_has_a_specific_classification():
    label, confidence = nlp_service.classify_document(
        "QR Code Samples\nExample #1, Fixed Text\nExample #2, Printable V-Card\nBarcode fields",
        filename="QRCodeFieldExamples.pdf",
    )

    assert label == "QR/Barcode Reference"
    assert confidence > 0.5


def test_noisy_spacy_layout_entities_are_filtered_and_dates_are_normalized():
    class FakeNLP:
        def __call__(self, _text):
            return SimpleNamespace(ents=[
                SimpleNamespace(text="two full Sunday, July 1", label_="DATE"),
                SimpleNamespace(text="PDF", label_="ORG"),
                SimpleNamespace(text="WindJack Solutions, Inc.", label_="ORG"),
                SimpleNamespace(text="Main St. Buffalo", label_="GPE"),
                SimpleNamespace(text=" Square", label_="PERSON"),
                SimpleNamespace(text="Jeff Smith", label_="PERSON"),
            ])

    entities = nlp_service.extract_entities("layout text", nlp=FakeNLP())

    assert entities["dates"] == ["July 1"]
    assert entities["organizations"] == ["WindJack Solutions, Inc"]
    assert entities["locations"] == []
    assert entities["people"] == ["Jeff Smith"]


def test_invoice_table_and_payment_copy_are_not_entities():
    class FakeNLP:
        def __call__(self, _text):
            return SimpleNamespace(ents=[
                SimpleNamespace(text="QUANTITY", label_="ORG"),
                SimpleNamespace(text="Custom Web Application Development", label_="ORG"),
                SimpleNamespace(text="Quick Payment Scan", label_="ORG"),
                SimpleNamespace(text="Acme Digital Solutions", label_="ORG"),
                SimpleNamespace(text="the due date. Thank you!", label_="PERSON"),
                SimpleNamespace(text="Jane Doe", label_="PERSON"),
            ])

    text = (
        "DESCRIPTION\nQUANTITY\nUNIT PRICE\nAMOUNT\n"
        "Custom Web Application Development\n12\n$175.00\n$2100.00\n"
        "Quick Payment\nScan to pay online or view\ninvoice details.\n"
        "Please make the payment by the due date. Thank you!\n"
        "Acme Digital Solutions\nJane Doe"
    )
    entities = nlp_service.extract_entities(text, nlp=FakeNLP())

    assert entities["organizations"] == ["Acme Digital Solutions"]
    assert entities["people"] == ["Jane Doe"]


def test_regex_phone_extraction_ignores_tax_id_lines():
    entities = nlp_service.extract_entities(
        "Phone: +999 1234 5678\nTax ID: XX-000-111-222\n"
        "Other Phone: +999 8765 4321"
    )

    assert entities["phones"] == ["+999 1234 5678", "+999 8765 4321"]
    assert nlp_service.extract_entities(
        "Please make the payment by the due date. Thank you!"
    )["people"] == []
