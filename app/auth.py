"""Signed bearer-token authentication."""

from __future__ import annotations

from functools import wraps

from flask import current_app, g, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .errors import APIError
from .extensions import db
from .models import User


TOKEN_SALT = "logivoice-access-token-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def create_access_token(user: User) -> str:
    return _serializer().dumps({"sub": str(user.id), "ver": user.token_version})


def _user_from_token(token: str) -> User:
    try:
        payload = _serializer().loads(
            token,
            max_age=current_app.config["TOKEN_MAX_AGE_SECONDS"],
        )
    except SignatureExpired as error:
        raise APIError("token_expired", "The access token has expired.", 401) from error
    except BadSignature as error:
        raise APIError("invalid_token", "The access token is invalid.", 401) from error

    try:
        user_id = int(payload["sub"])
        token_version = int(payload["ver"])
    except (KeyError, TypeError, ValueError) as error:
        raise APIError("invalid_token", "The access token is invalid.", 401) from error

    user = db.session.get(User, user_id)
    if user is None or user.token_version != token_version:
        raise APIError("invalid_token", "The access token is invalid.", 401)
    return user


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise APIError("authentication_required", "A bearer access token is required.", 401)
        g.current_user = _user_from_token(token.strip())
        return view(*args, **kwargs)

    return wrapped
