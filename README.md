# LogiVoice Backend

Independent Flask backend MVP for logistics-document processing. The service exposes a
versioned REST API under `/api/v1`, persists metadata with SQLAlchemy, and stores uploaded
binary files through a local-development or S3-compatible storage adapter.

## Implemented MVP

- Flask application factory with environment configuration, explicit CORS origins, JSON
  responses, centralized errors, security headers, and structured logs.
- Expiring, signed bearer tokens for registration, login, logout, and the current-user
  endpoint. Every document, search, chat, analytics, and download request is ownership
  scoped.
- PDF, PNG, JPG/JPEG, and UTF-8 TXT uploads through multipart `files`; `pdfFile` is also
  accepted for compatibility with the original frontend. Files are size/signature/MIME
  checked, sanitized, stored under generated keys, and processed synchronously.
- PDF text extraction through pdfplumber/PyMuPDF with a Tesseract fallback for scanned pages,
  image OCR through pytesseract/Tesseract when available, TXT extraction, deterministic logistics
  classification, and regex-based invoice/customer/address/date/organization/location
  fields. Unsupported processor dependencies produce a persisted `failed` document status.
- Owned document listing, filtering, pagination, detail, deletion, authenticated download,
  text search, optional document-grounded Gemini chat, and data-backed overview analytics.
- Flask-Migrate/Alembic migrations. Production startup applies migrations; it does not
  recreate a database.
- Process-local limits protect authentication, uploads, search, and chat. Multi-instance
  deployments should add a shared edge or Redis-backed limiter for global enforcement.

## Local setup

Run these commands from this repository directory:

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env  # PowerShell; use cp on macOS/Linux
flask --app wsgi:app db upgrade
flask --app wsgi:app run --debug
```

The default development database and local files live below `instance/`, which is ignored
by git. The application creates `instance/` automatically on startup, so a clean checkout
does not need a committed database directory. `SECRET_KEY` should be replaced for any shared environment. `DATABASE_URL` may be
SQLite for local work or PostgreSQL in deployment; `postgres://` and `postgresql://` values
are normalized to the installed psycopg 3 driver.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `development`, `testing`, or `production`; production requires S3 storage. |
| `SECRET_KEY` | Signs bearer tokens; use a long random secret. |
| `DATABASE_URL` | SQLite or PostgreSQL connection URL. |
| `CORS_ORIGINS` | Comma-separated exact origins, such as the deployed frontend origin. |
| `MAX_UPLOAD_SIZE_MB` | Request and per-file upload limit; default 25. |
| `TOKEN_MAX_AGE_SECONDS` | Bearer token lifetime; default 3600. |
| `TESSERACT_PATH` | Optional installed OCR executable path, not the Windows installer. Leave blank on Render Docker. |
| `STORAGE_BACKEND` | `local` for development/tests or `s3` for persistent object storage. |
| `STORAGE_ROOT` | Local storage directory. Do not use Render local disk for production. |
| `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL` | S3-compatible bucket and optional endpoint settings. |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Optional explicit S3 credentials; platform credentials may also be used. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional server-side Gemini configuration using the supported `google-genai` SDK. Without a key chat returns 503. |
| `GEMINI_TIMEOUT_SECONDS` | Provider timeout, default 20 seconds, bounded to 1-25 seconds; no automatic SDK retries. |

Tesseract is an operating-system dependency for image and scanned-PDF OCR. Install it locally
and make `tesseract` available on `PATH`, or set `TESSERACT_PATH` to the executable. The
provided Dockerfile installs and verifies `tesseract-ocr`. This MVP does not select spaCy,
zbar, or server-side audio transcription, so their model/binary packages are intentionally
absent.

## Tests

Tests use temporary SQLite databases and local temporary storage:

```bash
pytest -q
```

The suite covers health, authentication, ownership boundaries, TXT processing, search and
pagination, upload validation, and unavailable Gemini behavior. It does not call external
Gemini or S3 services.
Chat diagnostics tests also mock provider errors, check request-ID correlation, and verify
that keys, document contents, questions, and raw provider errors do not enter chat logs.

## Deployment

`render.yaml` describes a Docker-based Flask Render web service and managed PostgreSQL database. Set
the S3-compatible variables and the exact frontend origin before deployment. Render's
filesystem is ephemeral; production must use `STORAGE_BACKEND=s3`. The start command runs
`flask db upgrade` before Gunicorn. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## API and architecture

- [API contract](docs/API.md)
- [OpenAPI contract](docs/openapi.yaml)
- [Architecture](docs/ARCHITECTURE.md)
- [Deployment and operations](docs/DEPLOYMENT.md)

The former unversioned routes from the original single-file prototype are not runtime
routes in this repository. The compatibility note and migration guidance are documented
in [docs/API.md](docs/API.md).
