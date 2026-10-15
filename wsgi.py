"""Gunicorn/Render WSGI entry point."""

from pathlib import Path

# Ensure a clean checkout can construct the default SQLite URL before Flask,
# Flask-Migrate, or a CLI command asks SQLAlchemy for a connection.
Path(__file__).resolve().parent.joinpath("instance").mkdir(parents=True, exist_ok=True)

from app import create_app

app = create_app()
