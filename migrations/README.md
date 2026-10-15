# Database migrations

This directory is managed by Flask-Migrate/Alembic. Apply committed migrations with:

```bash
flask --app wsgi:app db upgrade
```

Create a new migration after model changes with `flask --app wsgi:app db migrate -m
