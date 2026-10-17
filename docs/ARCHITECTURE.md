# Architecture

## Request flow

```text
Frontend / API client
        |
        v
Flask factory -> CORS, JSON errors, logging, security headers
        |
        v
Versioned blueprint -> bearer auth -> ownership-scoped query
        |                         |
        |                         +--> SQLAlchemy -> SQLite (local/tests)
        |                                             PostgreSQL (production)
        |
        +--> storage adapter -> local files (development/tests)
        |                      S3-compatible object storage (production)
        |
         +--> synchronous processor -> TXT / PDF text / image OCR
                               -> spaCy/regex entities + classification
                               -> optional QR/barcode decoding
```

## Application boundaries

- `app/__init__.py` owns construction and extension registration. It does not create
  production tables; deployment runs the migration command.
- `app/models.py` contains `User` and owned `Document` records. Binary content is never
  stored in the database. `storage_key` is generated from the authenticated user ID and a
  UUID, not from the submitted filename.
- `app/auth.py` creates `itsdangerous` timed tokens. A token contains only a user ID and
  token version. Passwords are hashed with Werkzeug. Logout increments the token version,
  invalidating issued tokens for that account; expiry remains enforced by the signature.
- `app/services/processing.py` keeps extraction, NLP, classification, and barcode decoding
  independent from HTTP. spaCy is loaded lazily when its small model is installed; regex
  fallback remains available. Classification returns `Unknown` for weak evidence rather than
  forcing a category.
- `app/services/nlp.py` returns JSON-safe people, organization, location, date, contact, and
  URL entities. `app/services/barcode.py` decodes QR/barcode symbols from images and rasterized
  PDF pages when `pyzbar` and the system ZBar library are available.
- `app/services/logistics.py` extracts labeled totals, currencies, due dates, shipment/PO
  references, and supported product-list/table rows from each logical text segment. It
  preserves decimals as strings in the existing JSON fields and does not infer missing
  financial values or make remote AI calls. See the supported formats in `API.md`.
- `app/services/storage.py` provides the local and optional S3-compatible implementations.
  The factory rejects local storage in `APP_ENV=production` to avoid treating Render's
  ephemeral disk as durable storage.
- `app/services/chat.py` imports Gemini only when a request is made and a key is present.
  The document context is taken from an already ownership-checked database record.

## Persistence and migrations

The initial schema is in `migrations/versions/0001_initial.py`. New schema changes must
be generated and reviewed with Flask-Migrate. Render runs `flask db upgrade`; it does not
call `create_all`, drop tables, or recreate a production database. SQLite is used only for
local development and tests.

## Processing behavior and limits

Processing is synchronous for the MVP. Each logical document is persisted with `processing`,
then becomes `processed` or `failed` with a safe status message. UTF-8 TXT uploads with
repeated, uniquely numbered invoice headers can produce multiple logical records; each record
keeps an independently addressable copy of the original source binary for download. Direct
text extraction is used for PDFs first; pages without embedded text are rasterized and sent
through Tesseract OCR.
Image OCR also requires the Tesseract executable. QR/barcode detection is best-effort and does
not fail a document when ZBar is unavailable. Search is indexed ownership filtering plus
SQL `LIKE` queries over filename, extracted text, classification, and serialized extracted
fields; there is no vector index.
