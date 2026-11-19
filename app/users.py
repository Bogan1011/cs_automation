"""Team sign-ins kept in the database with scrypt-hashed passwords (set with `python -m app.cli set-password`).
DASHBOARD_USER / DASHBOARD_USERS in .env keep working alongside them."""
import base64
import hashlib
import hmac
import os

from sqlalchemy import select

from .config import settings
from .db import SessionLocal, User

ROLES = ("admin", "member")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def authenticate(username: str, password: str) -> str | None:
    """The canonical user name if the sign-in is right, else None."""
    name = (username or "").strip().lower()
    with SessionLocal() as s:
        u = s.scalars(select(User).where(User.name == name, User.active.is_(True))).first()
        if u and u.pw_hash:
            return u.name if check_password(password, u.pw_hash) else None
    env = settings.users
    match = next((k for k in env if hmac.compare_digest(name.encode(), k.lower().encode())), None)
    if match is not None and hmac.compare_digest(password.encode(), env[match].encode()):
        return match
    return None


def all_names() -> list[str]:
    with SessionLocal() as s:
        db = set(s.scalars(select(User.name).where(User.active.is_(True))))
    return sorted(db | set(settings.users))


def set_password(name: str, password: str, role: str = "admin") -> bool:
    """Create or update a user. Returns True when the user is new."""
    name = name.strip().lower()
    with SessionLocal() as s:
        u = s.scalars(select(User).where(User.name == name)).first()
        new = u is None
        if new:
            u = User(name=name)
            s.add(u)
        u.pw_hash, u.role, u.active = hash_password(password), role if role in ROLES else "admin", True
        s.commit()
        return new
