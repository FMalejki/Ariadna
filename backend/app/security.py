"""Authentication (JWT bearer, scrypt password hashes) and role-based authorization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionDep, utcnow
from app.domain import Role
from app.models import User

_bearer = HTTPBearer(auto_error=False)
_ephemeral_secret = secrets.token_urlsafe(48)

ROLE_RANK = {Role.VIEWER.value: 1, Role.ANALYST.value: 2, Role.ADMIN.value: 3}


def _secret() -> str:
    return get_settings().jwt_secret or _ephemeral_secret


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(actual, expected)


def create_token(user: User) -> str:
    now = utcnow()
    ttl = timedelta(minutes=get_settings().jwt_ttl_minutes)
    return jwt.encode(
        {"sub": user.id, "username": user.username, "role": user.role, "iat": now, "exp": now + ttl},
        _secret(),
        algorithm="HS256",
    )


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None or user.disabled:
        # Equalize timing a little when the user is unknown.
        verify_password(password, "scrypt$AAAA$AAAA")
        return None
    return user if verify_password(password, user.password_hash) else None


def bootstrap_admin(session: Session) -> bool:
    """Create the admin user only when OSINT_ADMIN_PASSWORD is configured. Never ships a default password."""
    s = get_settings()
    if not s.admin_password:
        return False
    existing = session.scalar(select(User).where(User.username == s.admin_username))
    if existing:
        return False
    session.add(User(username=s.admin_username, password_hash=hash_password(s.admin_password), role=Role.ADMIN.value))
    session.flush()
    return True


def current_user(
    session: SessionDep,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(creds.credentials, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    user = session.get(User, payload.get("sub"))
    if user is None or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown or disabled user")
    return user


def require_role(minimum: Role):
    def dependency(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role {minimum.value}")
        return user

    return dependency


viewer = require_role(Role.VIEWER)
analyst = require_role(Role.ANALYST)
admin = require_role(Role.ADMIN)
