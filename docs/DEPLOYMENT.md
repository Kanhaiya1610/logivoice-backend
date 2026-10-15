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

The Dockerfile installs and verifies the Tesseract OCR executable needed for image processing and all
Python dependencies. It does not install ffmpeg, zbar, or a spaCy model because this MVP
does not select server-side audio, barcode, or spaCy processing. Run it with PostgreSQL,
S3-compatible storage, and the required environment variables; do not mount Docker local
storage as the production document store.

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
```

Then upload a readable image or scanned PDF through the application to verify actual
OCR. The health endpoint checks the database, not OCR extraction. Native Python Render
deployments do not automatically run this Dockerfile; keep the Docker deployment type
to retain its system-package installation.

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
