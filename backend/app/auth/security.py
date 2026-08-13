"""Password hashing — the one place bcrypt is touched directly, mirroring how
semantics/llm_provider.py is the sole gateway to the Anthropic SDK (ADR-003's
"one wrapper per external primitive" pattern, applied to auth per ADR-007's
self-implemented design).
"""

import bcrypt

# bcrypt silently truncates (older versions) or raises (bcrypt>=4.0, pinned
# in pyproject.toml) on a password over 72 bytes — its own hash input limit,
# not a policy choice. Checked here so registration gets one clear 422
# instead of a cryptic ValueError surfacing from inside hashpw().
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
