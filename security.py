import os

import bcrypt

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
