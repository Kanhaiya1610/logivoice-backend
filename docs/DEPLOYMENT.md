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
