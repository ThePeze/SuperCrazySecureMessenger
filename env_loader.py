"""Load secrets from .env (same directory as this file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")


def get_fernet_key_bytes() -> bytes:
    key = os.environ.get("FERNET_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "FERNET_KEY is not set. Copy .env.example to .env and set FERNET_KEY "
            "(output of Fernet.generate_key() as a string)."
        )
    return key.encode()


def get_flask_secret() -> str:
    s = os.environ.get("FLASK_SECRET_KEY", "").strip()
    if not s:
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Copy .env.example to .env and set FLASK_SECRET_KEY."
        )
    return s
