"""Lazy verified HTTPX trust configuration; no clients or credentials retained."""

from functools import lru_cache


@lru_cache(maxsize=1)
def verified_context():
    # Preserve HTTPX's default CA bundle. Import/construction remains safe
    # without loading certificates; only a real HTTP exchange asks for this.
    import ssl

    import certifi

    return ssl.create_default_context(cafile=certifi.where())
