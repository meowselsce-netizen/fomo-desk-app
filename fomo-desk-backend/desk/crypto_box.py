"""AES-256-GCM envelopes for Plan B / GitHub Pages data files.

Plain JSON never goes in the public repo. The 32-byte key lives only in
the URL fragment (#d=) and localStorage — never in the repo or published path.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def generate_key() -> bytes:
    return os.urandom(32)


def key_from_fragment(d: str) -> bytes:
    """Accept a raw base64url 32-byte key, or derive one from a legacy opaque string."""
    d = (d or "").strip()
    if not d:
        raise ValueError("empty data key")
    try:
        raw = b64url_decode(d)
        if len(raw) == 32:
            return raw
    except Exception:
        pass
    # legacy: treat #d= as opaque secret and derive a 32-byte key
    import hashlib
    return hashlib.sha256(d.encode("utf-8")).digest()


def encrypt_bytes(key: bytes, plaintext: bytes) -> dict[str, Any]:
    if len(key) != 32:
        raise ValueError("AES-256 key must be 32 bytes")
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return {
        "v": 1,
        "alg": "A256GCM",
        "iv": b64url_encode(iv),
        "ct": b64url_encode(ct),
    }


def decrypt_bytes(key: bytes, envelope: dict[str, Any]) -> bytes:
    if envelope.get("alg") != "A256GCM" or int(envelope.get("v") or 0) != 1:
        raise ValueError("unsupported envelope")
    iv = b64url_decode(envelope["iv"])
    ct = b64url_decode(envelope["ct"])
    return AESGCM(key).decrypt(iv, ct, None)


def encrypt_json(key: bytes, obj: Any) -> dict[str, Any]:
    return encrypt_bytes(key, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def decrypt_json(key: bytes, envelope: dict[str, Any]) -> Any:
    return json.loads(decrypt_bytes(key, envelope).decode("utf-8"))
