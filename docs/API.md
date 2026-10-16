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
| `POST /documents` | Yes | Multipart `files`; also accepts `pdfFile`. Supports PDF, PNG, JPG/JPEG, UTF-8 TXT. |
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
so the response includes `processed` or `failed` status and any safe processing message.

Classification labels are `Invoice`, `Delivery Note`, `Packing Slip`, `Purchase Order`,
or `Unknown`. Low-evidence text receives `Unknown`. Regex fields include invoice number,
customer, address, date, organizations, and locations; unavailable extraction dependencies
produce a failed status rather than a fabricated result.

## Ownership and errors

Document IDs from another user are returned as 404 to avoid resource enumeration. Missing
or malformed bearer tokens return 401. Chat returns 503 when Gemini is not configured,
503 for provider quota/service unavailability, 504 for provider timeout, 502 for other provider
request failures, and 422 when the selected document has no text. Provider errors include
safe `details.reason`, `details.retryable`, and `details.request_id` fields. Each response
also carries a server-generated `X-Request-ID` header, exposed through CORS for the frontend.
These fields never include raw provider messages, keys, or document contents.

The original prototype exposed unversioned `/api/upload`, `/api/chat`, and history routes.
They are intentionally not aliases in this independent MVP; clients should migrate to the
versioned contracts above.
