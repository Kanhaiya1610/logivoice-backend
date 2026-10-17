# API contract

Base path: `/api/v1`. Unless stated otherwise, responses use:

```json
{"success": true, "data": {}, "message": "optional", "meta": {}}
```

Errors use:

```json
{"success": false, "error": {"code": "...", "message": "...", "details": {}}}
```

## Authentication

Register or log in to receive an expiring signed token. Send it on protected requests as:

```text
Authorization: Bearer <access_token>
```

`POST /auth/logout` increments the account token version and invalidates issued tokens.
There is no server-side token blacklist. Passwords are never returned or logged.

## Endpoints

| Method and path | Auth | Behavior |
| --- | --- | --- |
| `GET /health` | No | Database probe and service status. Returns 503 when the probe fails. |
| `POST /auth/register` | No | JSON `{email,password,name?}`; password minimum is 8 characters. |
| `POST /auth/login` | No | JSON `{email,password}`; returns user and token. |
| `POST /auth/logout` | Yes | Invalidates the current token version. |
| `GET /auth/me` | Yes | Returns the authenticated user. |
| `POST /documents` | Yes | Multipart `files`; also accepts `pdfFile`. Supports PDF, PNG, JPG/JPEG, UTF-8 TXT. Repeated uniquely numbered invoice records in TXT uploads are returned as separate logical documents. |
| `POST /documents/{id}/reprocess` | Yes | Re-runs extraction and classification against the owned original binary in storage. |
| `GET /documents` | Yes | Query `q`, `status`, `page`, `per_page` (maximum 100). |
| `GET /documents/{id}` | Yes | Owned document metadata, fields, and extracted text. |
| `DELETE /documents/{id}` | Yes | Deletes owned metadata and binary storage object. |
| `GET /documents/{id}/download` | Yes | Streams the owned original binary with its stored MIME type. |
| `POST /search` | Yes | JSON `{q, status?, page?, per_page?}`; searches only the user's documents. |
| `POST /chat` | Yes | JSON `{document_id, question}`; uses the owned document as Gemini context. |
| `GET /analytics/overview` | Yes | Counts the authenticated user's documents by status and classification, including `processed_documents`, `processing_documents`, `failed_documents`, `by_status`, and `by_classification`. |

## Upload details

The default request/per-file limit is 25 MB and is configured by `MAX_UPLOAD_SIZE_MB`.
The API checks sanitized extension, compatible declared MIME type, magic bytes, image
decodability, and UTF-8 for text. A bad file returns 400; an oversized request/file
returns 413. Each accepted file gets a generated storage key. Processing is synchronous,
so the response includes `processed` or `failed` status and any safe processing message. A
UTF-8 TXT upload with repeated, distinct `Invoice No.` or `Invoice Number` headers is
conservatively split into logical records; each record retains the original source binary
for download.

Classification labels include `Invoice`, `Delivery Note`, `Packing Slip`, `Purchase Order`,
`Resume/CV`, `Receipt`, or `Unknown`. Classification uses document content first; a filename
is only a weak hint and cannot classify weak content. Low-evidence text receives `Unknown`.
Fields include invoice number, customer, address, date, regex/spaCy entities, and a best-effort
`barcodes` list. Barcode records contain `type`, `data`, and a one-based `page` for PDFs.
Unavailable NLP/barcode dependencies use the regex/empty-list fallback and do not fail an
otherwise valid document.

## Logistics metadata

New uploads and reprocessing also populate the following additive keys in `fields`:

| Key | Value |
| --- | --- |
| `subtotal`, `tax`, `total` | Exact decimal strings, or `null` when missing, malformed, or conflicting. |
| `currency` | Recognized uppercase ISO currency code, or `null` when unknown or conflicting. Bare `$`/`¥` alone is not enough. |
| `due_date` | Explicitly labeled numeric or English-month date text, retained in its source format; no date-order inference. |
| `purchase_order_number`, `tracking_number`, `carrier` | Explicitly labeled reference text; leading zeros are retained. Missing values are `null`. |
| `line_items` | Array of `{description, sku, quantity, unit, unit_price, amount}`; empty when no supported rows are found. |

For example, `fields` may include:

```json
{
  "subtotal": "125.50",
  "tax": "12.55",
  "total": "138.05",
  "currency": "USD",
  "due_date": "October 31, 2026",
  "purchase_order_number": "000042",
  "tracking_number": "001234567890",
  "carrier": "Example Carrier",
  "line_items": [
    {"description": "Widget A", "sku": "0007", "quantity": "2", "unit": "pcs", "unit_price": "50.00", "amount": "100.00"},
    {"description": "Widget B", "sku": "SKU-0008", "quantity": "3", "unit": "pcs", "unit_price": "8.50", "amount": "25.50"}
  ]
}
```

Extraction uses the existing text/OCR result, with no new AI calls or schema migration.
Each logical TXT record is processed separately. Supported products are explicit
`Product List:`, `Items:`, or `Line Items:` sections such as `Widget A 10 pcs`, and
pipe/tab/multiple-space tables with recognized Description/Product, SKU, Qty/Quantity,
Unit, Unit Price, and Amount headers. Blank lines, new labels, or lost column alignment
end a section. Missing cells are `null`; invalid numeric cells reject that row. At most
200 items are returned. For PDFs with a visually aligned `DESCRIPTION`, `QUANTITY`,
`UNIT PRICE`, and `AMOUNT` table, PyMuPDF word coordinates are used to canonicalize
the rows before extraction. Arbitrary PDF/OCR layouts without those aligned columns
are not reconstructed.

Amounts preserve printed decimal precision: `1,234.50` and `1.234,50` normalize to
`1234.50`. A lone separator followed by exactly three digits (for example `1,234` or
`1.234`) is deliberately treated as ambiguous. Space-grouped numbers, exponents,
percentages used as amounts, and accounting parentheses are unsupported. No missing
prices, taxes, totals, or currencies are computed or guessed.

Total label priority is Grand Total, Invoice Total, Total Amount, Amount Due, then Total.
Total Tax outranks Tax/Sales Tax/VAT. Conflicting or malformed winning values remain
`null`. Explicit due-date text is not calendar-validated. Unrecognized layouts remain
available in `extracted_text` for review. Existing documents need **Reprocess** (or a
new upload) to acquire these fields; reading an older record does not modify it.

## Ownership and errors

Document IDs from another user are returned as 404 to avoid resource enumeration. Missing
or malformed bearer tokens return 401. Chat returns 503 when Gemini is not configured,
503 for provider quota/service unavailability, 504 for provider timeout, 502 for other provider
request failures, and 422 when the selected document has no text. Provider errors include
safe `details.reason`, `details.retryable`, and `details.request_id` fields. Each response
also carries a server-generated `X-Request-ID` header, exposed through CORS for the frontend.
These fields never include raw provider messages, keys, or document contents.

If a document record remains in the database but its original storage object is missing,
`GET /documents/{id}` still returns its stored metadata and extracted text, while
reprocess and download return `404` with `error.code` `file_not_found`. The record can be
deleted safely and the original file must be uploaded again; the backend does not invent
or reconstruct the missing binary. Storage outages use `storage_unavailable` (`503`) and
do not silently delete the record.

The original prototype exposed unversioned `/api/upload`, `/api/chat`, and history routes.
They are intentionally not aliases in this independent MVP; clients should migrate to the
versioned contracts above.
