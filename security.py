import os
import time

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