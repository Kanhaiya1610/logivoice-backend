from __future__ import annotations

import pytest

from app.services.logistics import extract_logistics_fields
from app.services.processing import extract_fields, split_logical_records


EMPTY = {
    "subtotal": None, "tax": None, "total": None, "currency": None,
    "due_date": None, "purchase_order_number": None, "tracking_number": None,
    "carrier": None, "line_items": [],
}
WIDGET = {
    "description": "Widget A", "sku": "0007", "quantity": "2", "unit": "pcs",
    "unit_price": "10.00", "amount": "20.00",
}


def test_realistic_invoice_merges_additive_fields_and_preserves_existing_fields():
    text = """Invoice Number: INV-123
Customer Name: Acme Logistics
Delivery Address: 10 Main Street
Invoice Date: 2026-10-01
Due Date: October 31, 2026
Purchase Order Number: 000042
Tracking No.: 001234567890
Shipping Carrier: FedEx
Currency: USD
Line Items:
SKU | Product | Qty | Unit | Unit Price | Amount
0007 | Widget A | 2 | pcs | 10.00 | 20.00
Subtotal: 125.50
Tax (10%): 12.55
Grand Total: USD 138.05
"""

    fields = extract_fields(text, entities={"organizations": ["Acme"], "locations": ["Boston"]})

    assert fields == {
        "invoice_number": "INV-123", "customer_name": "Acme Logistics",
        "address": "10 Main Street", "date": "2026-10-01",
        "organizations": ["Acme"], "locations": ["Boston"],
        "subtotal": "125.50", "tax": "12.55", "total": "138.05", "currency": "USD",
        "due_date": "October 31, 2026", "purchase_order_number": "000042",
        "tracking_number": "001234567890", "carrier": "FedEx", "line_items": [WIDGET],
    }


@pytest.mark.parametrize("text", ["", " \r\n\t", None, 42, b"Total: 42"])
def test_missing_or_nontext_input_has_all_extra_keys(text):
    assert extract_logistics_fields(text) == EMPTY


@pytest.mark.parametrize(("source", "expected"), [
    ("0", "0"), ("+000", "0"), ("-0.00", "0.00"), ("00012.50", "12.50"),
    ("1,234.50", "1234.50"), ("1.234,50", "1234.50"), ("12,50", "12.50"),
    ("1234.5678", "1234.5678"), ("-12,345.67890", "-12345.67890"),
    ("1,234,567", "1234567"), ("1.234.567", "1234567"),
    ("123456789012345678901234567890.1234567890", "123456789012345678901234567890.1234567890"),
])
def test_finite_decimal_strings_preserve_precision(source, expected):
    fields = extract_logistics_fields(f"Subtotal: {source}\nTax: {source}\nTotal: {source}")
    assert [fields[key] for key in ("subtotal", "tax", "total")] == [expected] * 3


@pytest.mark.parametrize("source", [
    "NaN", "sNaN", "Infinity", "-inf", "1e3", "1,234", "1.234", "1234.567",
    "1,23,4.50", "12.34,56", "1234,567.89", "1,234.5.0", "1.234,5,0",
    "1 234.50", "12..50", "12,", ".50", "20%", "10.00 20.00", "10-20",
    "SKU-0007", "tracking 001234", "USD NaN", "10.00 USD EUR", "(10.00)", "9" * 101,
    "$$10.00", "USD USD 10.00", "$10.00$",
])
def test_ambiguous_malformed_and_nonfinite_amounts_are_not_salvaged(source):
    fields = extract_logistics_fields(f"Subtotal: {source}\nTax: {source}\nTotal: {source}")
    assert all(fields[key] is None for key in ("subtotal", "tax", "total"))


def test_subtotal_tax_rates_and_identifiers_do_not_invent_a_total():
    fields = extract_logistics_fields(
        "Subtotal: 125.50\nTax: 10%\nVAT Rate: 20\n"
        "PO: 0007\nTracking Number: 0012345\nSKU: 000012\n"
        "Tax ID: XX-000-111-222\nNotes: invoice total is 138.05\nThe total is 138.05"
    )
    assert fields["subtotal"] == "125.50"
    assert fields["tax"] is None
    assert fields["total"] is None
    assert fields["purchase_order_number"] == "0007"
    assert fields["tracking_number"] == "0012345"


def test_tax_id_is_not_treated_as_a_tax_amount():
    assert extract_logistics_fields(
        "Tax ID: XX-000-111-222\nTax (8.5%): 327.04"
    )["tax"] == "327.04"


@pytest.mark.parametrize("label", ["Subtotal", "Sub Total", "Sub-Total"])
def test_subtotal_labels_never_match_total(label):
    fields = extract_logistics_fields(f"{label}: 125.50")
    assert fields["subtotal"] == "125.50"
    assert fields["total"] is None


@pytest.mark.parametrize("label", ["Tax", "Sales Tax", "VAT", "VAT (10%)", "VAT 10%"])
def test_tax_labels_with_separate_amounts(label):
    assert extract_logistics_fields(f"{label}: 12.55")["tax"] == "12.55"


@pytest.mark.parametrize("label", ["Tax: 10%", "VAT (20%)", "Sales Tax: 5.00 %", "Tax Rate: 12.55",
                                   "Tax: 10%12.55", "VAT: (20%)12.55"])
def test_tax_rates_without_amounts_remain_null(label):
    assert extract_logistics_fields(label)["tax"] is None


@pytest.mark.parametrize("reverse", [False, True])
def test_total_label_priority_is_independent_of_line_order(reverse):
    lines = ["Total: 1.00", "Amount Due: 2.00", "Total Amount: 3.00",
             "Invoice Total: 4.00", "Grand Total: 5.00"]
    if reverse:
        lines.reverse()
    assert extract_logistics_fields("\n".join(lines))["total"] == "5.00"


@pytest.mark.parametrize("label", ["Total", "Amount Due", "Total Amount", "Invoice Total", "Grand Total"])
def test_each_total_label_is_recognized(label):
    assert extract_logistics_fields(f"{label} 138.05")["total"] == "138.05"


def test_conflicting_repeated_or_invalid_preferred_amounts_remain_null():
    assert extract_logistics_fields("Total: 10.00\nTotal: 11.00")["total"] is None
    assert extract_logistics_fields("Total: 10.00\nGrand Total: NaN")["total"] is None
    assert extract_logistics_fields("Total: 10.00\nTotal:")["total"] is None
    assert extract_logistics_fields("Total: 10.00\nTotal: 10")["total"] == "10.00"
    assert extract_logistics_fields("Tax: 2.00\nVAT: 3.00")["tax"] is None
    assert extract_logistics_fields("Tax: 2.00\nVAT: 3.00\nTotal Tax: 5.00")["tax"] == "5.00"


@pytest.mark.parametrize(("text", "expected"), [
    ("Total: $138.05", None), ("Currency: $\nTotal: $138.05", None),
    ("Total: ¥138.05", None), ("Currency: usd\nTotal: $138.05", "USD"),
    ("Total: USD 138.05", "USD"), ("Total: 138.05 eur", "EUR"),
    ("Total: €138.05", "EUR"), ("Total: £138.05", "GBP"),
    ("Total: US$138.05", "USD"), ("Total: C$138.05", "CAD"),
    ("Currency: USD\nSubtotal: EUR 125.50", None),
    ("Total: USD 138.05\nTax: EUR 12.55", None),
    ("Currency: ZZZ\nTotal: 138.05", None),
    ("We accept USD and EUR.\nSKU: USD\nTotal: 138.05", None),
    ("Total: USD NaN", None),
])
def test_currency_requires_unambiguous_relevant_evidence(text, expected):
    assert extract_logistics_fields(text)["currency"] == expected


def test_empty_labels_do_not_consume_the_next_line():
    fields = extract_logistics_fields(
        "Subtotal:\n125.50\nTax:\n12.55\nTotal:\nUSD 138.05\nCurrency:\nEUR\n"
        "Due Date:\n2026-10-31\nPurchase Order No.:\n00042\n"
        "Tracking ID:\n0012345\nCarrier:\nFedEx"
    )
    assert fields == EMPTY


def test_fields_are_line_anchored_and_do_not_extract_inline_prose():
    assert extract_logistics_fields(
        "Please use PO: 00042\nYour Tracking No: 001234\n"
        "Estimated Due Date: 10/31/2026\nOur Carrier: UPS\n"
        "Notes: Subtotal: 125.50 Tax: 12.55 Total: USD 138.05"
    ) == EMPTY


@pytest.mark.parametrize("value", ["01/02/2026", "2026-10-31", "31.10.2026", "10/31/26",
                                   "31 October 2026", "Oct 31st, 2026"])
def test_explicit_due_dates_retain_source_format(value):
    assert extract_logistics_fields(f"Due Date: {value}")["due_date"] == value
    assert extract_logistics_fields(f"Payment Due: {value}")["due_date"] == value


@pytest.mark.parametrize("text", ["Invoice Date: 2026-10-31", "Payment Terms: Net 30",
                                  "Due Date: Net 30", "Due Date: 99/99/2026",
                                  "Due Date: 2026-10-31 extra", "Due Date: Tracking No: 00123"])
def test_due_date_is_not_inferred_or_taken_from_another_label(text):
    assert extract_logistics_fields(text)["due_date"] is None


@pytest.mark.parametrize("label", ["PO", "PO Number", "PO No.", "PO#", "P.O. #",
                                   "Purchase Order", "Purchase Order No.", "Purchase Order Number"])
def test_purchase_order_references_preserve_leading_zeros(label):
    assert extract_logistics_fields(f"{label}: 000042-A")["purchase_order_number"] == "000042-A"


@pytest.mark.parametrize("label", ["Tracking No.", "Tracking Number", "Tracking ID", "AWB",
                                   "AWB Number", "Waybill", "Waybill No."])
def test_tracking_references_preserve_leading_zeros(label):
    assert extract_logistics_fields(f"{label}: 001-00042")["tracking_number"] == "001-00042"


def test_reference_conflicts_and_multiple_tokens_are_not_guessed():
    assert extract_logistics_fields("PO: 0001\nPO: 0002")["purchase_order_number"] is None
    assert extract_logistics_fields("Tracking No: 0001 / 0002")["tracking_number"] is None
    assert extract_logistics_fields("Carrier: Tracking No: 0001")["carrier"] is None
    assert extract_logistics_fields("Carrier: UPS Ground")["carrier"] == "UPS Ground"


@pytest.mark.parametrize("section", ["Product List:", "Items:", "Line Items:"])
def test_simple_product_lists_do_not_guess_skus_or_prices(section):
    fields = extract_logistics_fields(f"{section}\nWidget A 10 pcs\nGadget B 5 boxes\nSubtotal: 125.50")
    assert fields["line_items"] == [
        {"description": "Widget A", "sku": None, "quantity": "10", "unit": "pcs",
         "unit_price": None, "amount": None},
        {"description": "Gadget B", "sku": None, "quantity": "5", "unit": "boxes",
         "unit_price": None, "amount": None},
    ]
    assert fields["total"] is None


@pytest.mark.parametrize("separator", [" | ", "\t", "  "])
def test_header_mapped_tables_support_all_delimiters_and_reordered_columns(separator):
    header = separator.join(["SKU", "Description", "Quantity", "Unit", "Unit Price", "Amount"])
    row = separator.join(["0007", "Widget A", "2", "pcs", "10.00", "20.00"])
    assert extract_logistics_fields(f"{header}\n{row}")["line_items"] == [WIDGET]


def test_wrapped_pipe_table_with_rule_and_european_money():
    fields = extract_logistics_fields(
        "| Product | Amount | Qty | SKU | Unit Price | Unit |\n"
        "| --- | ---: | --- | --- | ---: | --- |\n"
        "| Widget A | EUR 1.234,50 | 02 | 0007 | 617,25 | pcs |"
    )
    assert fields["line_items"] == [{**WIDGET, "unit_price": "617.25", "amount": "1234.50"}]
    assert fields["currency"] == "EUR"


@pytest.mark.parametrize("separator", ["|", "\t"])
def test_empty_table_cells_stay_null_and_zero_is_not_missing(separator):
    header = separator.join(["Description", "SKU", "Qty", "Unit", "Unit Price", "Amount"])
    row = separator.join(["Widget A", "", "0", "", "0.00", ""])
    assert extract_logistics_fields(f"{header}\n{row}")["line_items"] == [{
        "description": "Widget A", "sku": None, "quantity": "0", "unit": None,
        "unit_price": "0.00", "amount": None,
    }]


@pytest.mark.parametrize(("quantity", "price", "amount"), [
    ("two", "10.00", "20.00"), ("NaN", "10.00", "20.00"), ("2", "Infinity", "20.00"),
    ("2", "10.00", "1,234"), ("2", "SKU 0007", "20.00"), ("2", "10.00", "10%"),
])
def test_malformed_table_numeric_cells_reject_the_whole_row(quantity, price, amount):
    fields = extract_logistics_fields(
        "SKU|Product|Qty|Unit|Unit Price|Amount\n"
        f"0008|Bad widget|{quantity}|pcs|{price}|{amount}\n"
        "0007|Widget A|2|pcs|10.00|20.00"
    )
    assert fields["line_items"] == [WIDGET]


@pytest.mark.parametrize("text", [
    "Widget A 10 pcs\nWe delivered 5 boxes",  # No explicit section.
    "Notes: Widget A 10 pcs\nPlease deliver 5 boxes",
    "Items:\nThe products are described in the attached list.\nWidget A 10 pcs",
    "Product List:\nWidget A 10 pcs 10.00 100.00",  # No price positions guessed.
    "Items:\nWidget A NaN pcs\nWidget B 2 pcs",
    "Product|Qty|Quantity\nWidget A|2|2",  # Duplicate mapped quantity.
    "Product|SKU|Unknown Column\nWidget A|0007|2",
    "Description  Qty  Amount\nWidget A  2  pcs  20.00",  # Lost column alignment.
    "Description|Qty|Amount\nWidget A|2|20.00|unexpected\nWidget B|2|20.00",
    "Description|Qty|Amount\n12345|2|20.00",  # No product description.
])
def test_prose_and_ambiguous_products_are_not_extracted(text):
    assert extract_logistics_fields(text)["line_items"] == []


@pytest.mark.parametrize("stop", ["Total: 20.00", "Subtotal 20.00", "Tracking ID: 00042",
                                  "Due Date: 2026-10-31", "Notes: deliver soon", ""])
def test_simple_lists_stop_on_totals_labels_and_blank_lines(stop):
    fields = extract_logistics_fields(f"Items:\nWidget A 2 pcs\n{stop}\nWidget B 5 pcs")
    assert [item["description"] for item in fields["line_items"]] == ["Widget A"]


def test_table_stops_on_labels_without_scanning_later_rows_or_inventing_totals():
    fields = extract_logistics_fields(
        "Product|Qty|Unit Price|Amount\nWidget A|2|10.00|20.00\n"
        "Tax: 2.00\nWidget B|2|10.00|20.00"
    )
    assert fields["line_items"] == [{**WIDGET, "sku": None, "unit": None}]
    assert fields["subtotal"] is None
    assert fields["total"] is None


def test_number_of_items_and_oversized_header_work_are_bounded():
    fields = extract_logistics_fields("Items:\n" + "Widget A 10 pcs\n" * 205 + "Total: 99.00")
    assert len(fields["line_items"]) == 200
    assert fields["total"] == "99.00"
    assert extract_logistics_fields(" " * 513 + "Product|Qty\nWidget A|2")["line_items"] == []


def test_logical_segments_keep_logistics_metadata_local():
    segments = split_logical_records(
        "Invoice Number: INV-001\nItems:\nWidget A 2 pcs\n"
        "Invoice Number: INV-002\nTotal: USD 138.05\nTracking ID: 00042"
    )
    assert len(segments) == 2
    first, second = [extract_fields(segment, entities={"organizations": []}) for segment in segments]
    assert first["total"] is None
    assert first["tracking_number"] is None
    assert len(first["line_items"]) == 1
    assert second["total"] == "138.05"
    assert second["tracking_number"] == "00042"
    assert second["line_items"] == []
