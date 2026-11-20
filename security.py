import math
import os
import threading
import time
from collections import deque

import bcrypt
import jwt

ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
MAX_BYTES = 72


def hash_password(password):
    raw = password.encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=ROUNDS)).decode("ascii")


def verify_password(password, password_hash):
    raw = password.encode("utf-8")
    if len(raw) > MAX_BYTES:
        return False
    return bcrypt.checkpw(raw, password_hash.encode("ascii"))


DUMMY_HASH = hash_password("dummy-password")


def dummy_verify(password):
    verify_password(password, DUMMY_HASH)


ALGORITHM = "HS256"


def jwt_secret():
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("Не задан JWT_SECRET: добавь его в файл .env")
    return secret


def token_lifetime():
    raw = os.environ.get("JWT_EXPIRES_MINUTES", "30")
    try:
        minutes = int(raw)
    except ValueError:
        raise RuntimeError("JWT_EXPIRES_MINUTES должно быть целым числом")
    if minutes <= 0:
        raise RuntimeError("JWT_EXPIRES_MINUTES должно быть больше нуля")
    return minutes * 60


def check_settings():
    jwt_secret()
    token_lifetime()


def create_token(user_id):
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + token_lifetime()}
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM)


def decode_token(token):
    payload = jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    try:
        return int(payload["sub"])
    except (TypeError, ValueError):
        raise jwt.InvalidTokenError("sub")


MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW_SECONDS = 60


class LoginLimiter:
    def __init__(self, max_failures, window):
        self.max_failures = max_failures
        self.window = window
        self.failures = {}
        self.lock = threading.Lock()

    def _prune(self, key):
        attempts = self.failures.get(key)
        if attempts is None:
            return None
        border = time.monotonic() - self.window
        while attempts and attempts[0] <= border:
            attempts.popleft()
        if not attempts:
            del self.failures[key]
            return None
        return attempts

    def retry_after(self, key):
        with self.lock:
            attempts = self._prune(key)
            if attempts is None or len(attempts) < self.max_failures:
                return 0
            return max(1, math.ceil(attempts[0] + self.window - time.monotonic()))

    def register_failure(self, key):
        with self.lock:
            if self._prune(key) is None:
                self.failures[key] = deque()
            self.failures[key].append(time.monotonic())
            if len(self.failures) > 5000:
                for other in list(self.failures):
                    self._prune(other)

    def reset(self, key):
        with self.lock:
            self.failures.pop(key, None)


login_limiter = LoginLimiter(MAX_LOGIN_FAILURES, LOGIN_WINDOW_SECONDS)