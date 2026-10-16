# Deployment and operations

## Render

1. Create a new Render Blueprint from this repository. The blueprint deploys the included
   Dockerfile so the Tesseract OCR executable is installed during the image build.
2. Provision managed PostgreSQL and set `DATABASE_URL` (the blueprint connects it).
3. Set `APP_ENV=production`, generate a strong `SECRET_KEY`, and set `CORS_ORIGINS` to the
   exact HTTPS frontend origin(s), comma separated. Do not use `*`.
4. Configure an S3-compatible bucket using `STORAGE_BACKEND=s3`, `S3_BUCKET`, and the
   provider's region/endpoint/credentials. Keep credentials in Render environment
   variables or the provider's workload identity, never in source control.
5. Optionally set `GEMINI_API_KEY` and `GEMINI_MODEL`. The API intentionally reports chat
   as unavailable when this is not configured.
6. Deploy. The start command applies committed migrations before starting Gunicorn and the
   health check calls `/api/v1/health`.

The free Render web service can sleep and has cold starts. Its local filesystem is
ephemeral, so `STORAGE_BACKEND=local` is rejected in production. PostgreSQL and object
storage are separate persistence services.

## Migrations

The application creates the ignored local `instance/` directory automatically when it builds
the default SQLite configuration. CI and local validation therefore work from a clean checkout
without a committed database file or folder. Production uses the configured PostgreSQL URL.
The migration environment and `wsgi.py` also create the SQLite parent directory as a defensive
measure, and CI explicitly runs `mkdir -p instance` before migration validation.

Run locally:

```bash
flask --app wsgi:app db upgrade
flask --app wsgi:app db current
```

For a model change:

```bash
flask --app wsgi:app db migrate -m "describe schema change"
```

Review the generated Alembic operation, commit it, and let deployment run `db upgrade`.
Back up production before destructive migrations. Never replace this process with
`db.drop_all()` or `db.create_all()`.

## Docker

The Dockerfile installs and verifies the Tesseract OCR executable, installs the Linux ZBar
library for QR/barcode decoding, downloads the small `en_core_web_sm` spaCy model, and installs
the Python dependencies. It does not install ffmpeg because this MVP does not select
server-side audio transcription. Run it with PostgreSQL,
S3-compatible storage, and the required environment variables; do not mount Docker local
storage as the production document store.

PDF processing uses PyMuPDF first for fast embedded-text extraction. The slower `pdfplumber`
path is only a compatibility fallback when PyMuPDF is unavailable. Text extraction, scanned-PDF
OCR, and PDF barcode scanning are bounded by `MAX_PDF_PAGES` (default 25). Gunicorn allows up
to 120 seconds for a deliberately slow OCR request, but normal text PDFs should complete much
faster. This prevents complex PDF parsing from consuming a worker indefinitely.

## Tesseract: Windows installer versus deployed OCR engine

The file `tesseract-ocr-w64-setup-5.5.0.20241111.exe` is a Windows setup utility, not
an application dependency or the OCR executable. Keep it outside this repository; do
not upload it to GitHub or Render. Moving that installer does not change any backend
path, but it also does not install Tesseract. Ignore rules exclude matching installers
from Git and Docker builds as a safeguard.

### Local Windows development

Run the installer locally if Tesseract is not installed. In your local backend `.env`,
set `TESSERACT_PATH` to the installed `tesseract.exe` if it is not on your system `PATH`.
For example, only if this matches your actual installation directory:

```dotenv
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
```

Verify that executable in PowerShell:

```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
```

Do not point `TESSERACT_PATH` at the `*-setup-*.exe` file. Do not copy a Windows path
from your local `.env` into Render environment variables.

### Render deployment

`render.yaml` selects `runtime: docker`. During the image build, `Dockerfile` installs
the Linux `tesseract-ocr` package with `apt-get` and verifies it using
`tesseract --version`. It does not run or need the Windows installer. The package
version comes from the container's Linux package repository, not the Windows filename.

Leave `TESSERACT_PATH` unset or blank on Render. `pytesseract` finds the installed
Linux `tesseract` command through the container's `PATH` (normally `/usr/bin/tesseract`).
The backend calls it for image uploads and PDFs with no embedded text. Text-based PDFs
use direct extraction first. The current OCR calls request English (`lang="eng"`).

After deploying, use the Render service shell, where available, to check:

```bash
tesseract --version
tesseract --list-langs
python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
python -c "from app.services.barcode import barcode_dependency_status; print(barcode_dependency_status())"
python -c "import spacy; print(spacy.load('en_core_web_sm').meta['name'])"
```

Then upload a readable image or scanned PDF through the application to verify actual OCR,
entity extraction, and any QR/barcode fields. The health endpoint checks the database, not
OCR/NLP/barcode extraction. Native Python Render
deployments do not automatically run this Dockerfile; keep the Docker deployment type
to retain its system-package installation.

## Diagnosing document chat on Render

Open **Render → logivoice-backend → Logs** for runtime/service logs (not just build logs).
Choose a recent time window or live tail. Timestamps may be UTC; an old startup log does
not show what happened to a new browser request. A sleeping free service must wake first.
`GET /api/v1/health` returning 200 proves database connectivity, not Gemini availability.
`HEAD /` or `GET /` returning 404 is normal: there is no backend homepage.

After deploying the diagnostics changes:

1. Look for `chat_provider_configuration` at worker startup. It includes the configured
   model, `google-genai` SDK version, timeout, and an `api_key_configured` boolean, never
   the key value. There should be no `google.generativeai` deprecation warning after the
   new image is deployed.
2. Ask one question about an owned, already processed document through the frontend.
3. In browser developer tools, inspect **Network → POST /api/v1/chat**. Copy only the
   `X-Request-ID` response header or `error.details.request_id`. Do not copy authorization
   headers, document contents, or private request payloads into bug reports.
4. Filter Render runtime logs by that ID. Expected events are `chat_request_started` for
   the POST request,
   `chat_provider_failed` on failure, and `request_completed` with HTTP status/duration.
5. Use the safe `reason`, `provider_status`, `error_type`, and `model` fields to diagnose.

Illustrative failure event, not a captured production result:

```json
{"message":"chat_provider_failed","provider":"gemini","model":"configured-model-id","reason":"model_not_found","provider_status":404,"error_type":"NotFound","request_id":"server-generated-id","retryable":false}
```

| Logged reason | What to check |
| --- | --- |
| `not_configured` | Set `GEMINI_API_KEY` in Render, then deploy/restart. Never put it in the frontend. |
| `sdk_unavailable` | Confirm the deployed image installed the `google-genai` dependency from `requirements.txt`. |
| `sdk_initialization_failed` | The installed SDK could not import; check dependency/runtime compatibility. Render uses Python 3.12 from the Dockerfile; a shared local Python environment may differ. |
| `model_not_found` (404) | Verify `GEMINI_MODEL` exists and supports content generation for this project's API/version; choose a currently available model for the account. |
| `invalid_request` (400) | Check provider configuration/key validity and request constraints in the provider console. A 400 alone does not identify the exact invalid field. |
| `authentication_failed` / `permission_denied` (401/403) | Check key restrictions, project permissions, and provider access. |
| `quota_exceeded` (429) | Check quota/rate limits and billing in the provider console; do not retry in a loop. |
| `timeout` | Retry once later; reduce request size if persistent. |
| `service_unavailable` (503) | Check provider service status and retry later. |
| `empty_response` / `provider_error` | Check the error class, model availability, and provider safety/response behavior; the provider did not return a usable answer. |

The repository now defaults to `gemini-2.5-flash`. This is not an assertion that the model
is currently available: set `GEMINI_MODEL` explicitly to a model supported by your account.
The backend uses Google's supported `google-genai` SDK (`genai.Client` and
`client.models.generate_content`); it no longer uses the deprecated `google-generativeai`
package.
No fallback silently changes providers or models. No remote provider call is made at startup.
`GEMINI_TIMEOUT_SECONDS` defaults to 20 seconds, bounded to 1-25; SDK retries are disabled
to avoid silently occupying a worker for repeated requests.

These diagnostics intentionally do **not** log raw Gemini exception messages/response bodies,
API keys, bearer tokens, prompts, document text, or question text. Provider status, class,
and failure categories are logged instead. Keep SDK/HTTP wire-debug logging disabled.

If there is no `POST /api/v1/chat` event after retrying, check the browser's API URL/CORS,
the selected Render service, deployed revision, and log time window. If a start event has
no completion, inspect surrounding Gunicorn worker timeout/restart logs. The healthy startup
and health-probe lines alone cannot establish a cause for a failed chat request.

## Troubleshooting

- **Health is 503:** inspect the `DATABASE_URL`, database availability, and migration logs.
- **Image or scanned-PDF documents fail:** install Tesseract and verify `tesseract --version`,
  or set `TESSERACT_PATH`; the Python package alone is not the OCR engine.
- **Chat is 503:** set `GEMINI_API_KEY` and ensure the selected SDK/model is available. A
  provider outage is returned as a non-success API response rather than a fabricated answer.
- **CORS errors:** include the browser's exact origin, including scheme and port, in
   `CORS_ORIGINS`; CORS does not replace bearer authentication.
- **429 responses:** the application applies process-local limits to authentication, upload,
  search, and chat routes. For multiple backend instances, add a shared edge or Redis-backed
  rate limiter as an additional deployment control.
- **Files disappear after deploy:** configure S3-compatible storage. Render local disk is
  not persistent.
