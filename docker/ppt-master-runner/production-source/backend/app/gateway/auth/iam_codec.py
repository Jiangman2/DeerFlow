"""Cryptographic envelope used by the CNOOC IAM BIM account service.

The wire body is Base64(SM4-ECB-PKCS7(JSON)).  Request fields are signed with
SM3 after deterministic canonicalisation.  Keep this module free of FastAPI so
the protocol has focused unit-test coverage and cannot accidentally log raw
credentials or plaintext request bodies.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, modes

try:  # cryptography 48 moved legacy algorithms under ``decrepit``.
    from cryptography.hazmat.decrepit.ciphers.algorithms import SM4
except ImportError:  # pragma: no cover - supports the older pinned image too
    from cryptography.hazmat.primitives.ciphers.algorithms import SM4


class IAMCodecError(ValueError):
    """Protocol error safe to expose as a generic IAM request failure."""


_BLOCK_BITS = 128


def _key_bytes(value: str) -> bytes:
    raw = value.strip()
    if len(raw) == 32:
        try:
            decoded = bytes.fromhex(raw)
        except ValueError:
            decoded = b""
        if len(decoded) == 16:
            return decoded
    encoded = raw.encode("utf-8")
    if len(encoded) == 16:
        return encoded
    raise IAMCodecError("IAM SM4 key must be a 16-byte value or 32 hexadecimal characters")


def _cipher(key: str) -> Cipher:
    return Cipher(SM4(_key_bytes(key)), modes.ECB())


def sm3_hexdigest(value: str) -> str:
    try:
        digest = hashlib.new("sm3")
    except ValueError as exc:  # pragma: no cover - OpenSSL 3 images provide SM3
        raise IAMCodecError("SM3 is unavailable in this runtime") from exc
    digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def canonicalize_signed_fields(payload: Mapping[str, Any]) -> str:
    """Encode IAM fields deterministically, excluding the signature itself."""
    fields: list[str] = []
    for key in sorted(str(key) for key in payload if str(key) != "signature"):
        value = payload[key]
        if value is None:
            rendered = ""
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        else:
            rendered = str(value)
        fields.append(f"{key}={rendered}")
    return "&".join(fields)


def encrypt_json_response(payload: Mapping[str, Any], key: str) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    padder = padding.PKCS7(_BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = _cipher(key).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")


def decrypt_sm4_ecb(ciphertext: str | bytes, key: str) -> str:
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
        decryptor = _cipher(key).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(_BLOCK_BITS).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise IAMCodecError("IAM encrypted body is invalid") from exc


def decrypt_json_request(ciphertext: str | bytes, key: str) -> dict[str, Any]:
    try:
        payload = json.loads(decrypt_sm4_ecb(ciphertext, key))
    except json.JSONDecodeError as exc:
        raise IAMCodecError("IAM encrypted body is not JSON") from exc
    if not isinstance(payload, dict):
        raise IAMCodecError("IAM request JSON must be an object")
    return payload
