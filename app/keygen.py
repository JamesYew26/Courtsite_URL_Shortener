import secrets
import string

BASE62_ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase


def generate_key(length: int = 7) -> str:
    """Generate a cryptographically random Base62 short key."""
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(BASE62_ALPHABET) for _ in range(length))
