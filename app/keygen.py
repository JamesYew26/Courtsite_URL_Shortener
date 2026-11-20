import secrets
import string

# Base62 alphabet (0-9, a-z, A-Z) = 62 possible characters per position.
# Why Base62 instead of Base64? Base62 omits '+' and '/' which have special
# meaning in URLs and would require percent-encoding.
BASE62_ALPHABET = string.digits + string.ascii_lowercase + string.ascii_uppercase


def generate_key(length: int = 7) -> str:
    """Generate a cryptographically random Base62 short key.

    Design Decision Rationale:
    - Why 7-character Base62? 62^7 = 3,521,614,606,208 (~3.52 trillion) possible keys.
      This keeps collisions near-zero while keeping URLs short enough to render low-density,
      easy-to-scan physical QR codes on mall posters.
    - Why `secrets.choice` instead of `random.choice` or auto-incrementing IDs (1, 2, 3...)?
      Auto-incrementing integers are predictable/enumerable: competitors or bots could guess
      `/shorten_url/1`, `/shorten_url/2`, scrape all marketing links, and pollute BI analytics
      with artificial visits. `secrets.choice` uses the OS CSPRNG so keys cannot be guessed.
    """
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(secrets.choice(BASE62_ALPHABET) for _ in range(length))
