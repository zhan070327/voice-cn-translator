"""Password hashing and short-lived, revocable access sessions."""
import hashlib
import hmac
import secrets
import time

import jwt

from .store import Denied, digest


def _derive(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024 * 1024)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + _derive(password, salt).hex()


class Auth:
    def __init__(self, settings, store):
        self.key = settings.secrets["JWT_SECRET"]
        self.access_seconds = settings.access_seconds
        self.refresh_seconds = settings.refresh_seconds
        self.store = store
        self.dummy = hash_password(secrets.token_urlsafe(24))

    def verify_password(self, email, password):
        row = self.store.credentials(email)
        salt, expected = (row["password"] if row else self.dummy).split(":")
        matches = hmac.compare_digest(_derive(password, bytes.fromhex(salt)), bytes.fromhex(expected))
        if row is None or not matches:
            raise Denied(401, "unauthenticated")
        return row["id"]

    def _tokens(self, account, session, refresh, now):
        token = jwt.encode({"sub": account, "sid": session, "iat": now,
                            "exp": now + self.access_seconds, "iss": "samefreq-cloud"},
                           self.key, algorithm="HS256")
        return {"access_token": token, "refresh_token": refresh}

    def new_session(self, account):
        refresh, now = secrets.token_urlsafe(32), int(time.time())
        session = self.store.new_session(account, digest(refresh), now + self.refresh_seconds)
        return self._tokens(account, session, refresh, now)

    def refresh(self, old_token):
        refresh, now = secrets.token_urlsafe(32), int(time.time())
        account, session = self.store.rotate(digest(old_token), digest(refresh), now)
        return self._tokens(account, session, refresh, now)

    def logout(self, token):
        self.store.revoke(digest(token))

    def identify(self, authorization):
        if not authorization.startswith("Bearer ") or len(authorization) > 4096:
            raise Denied(401, "unauthenticated")
        try:
            data = jwt.decode(
                authorization[7:], self.key, algorithms=["HS256"], issuer="samefreq-cloud",
                options={"require": ["sub", "sid", "iat", "exp"]},
            )
        except jwt.InvalidTokenError:
            raise Denied(401, "unauthenticated") from None
        account, session = data["sub"], data["sid"]
        if not isinstance(session, str) or not self.store.valid_session(account, session, int(time.time())):
            raise Denied(401, "unauthenticated")
        return account
