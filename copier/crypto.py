"""Encryption for API credentials at rest (Fernet). Django-independent.

ENCRYPTION_KEYS is a comma-separated list of Fernet keys. The FIRST key encrypts;
all keys can decrypt, so you can rotate by prepending a new key.
Generate one with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet


def build_cipher(keys: str) -> MultiFernet:
    parts = [k.strip() for k in keys.split(",") if k.strip()]
    if not parts:
        raise ValueError("ENCRYPTION_KEYS is empty")
    return MultiFernet([Fernet(k.encode()) for k in parts])


def encrypt(cipher: MultiFernet, plaintext: str) -> str:
    return cipher.encrypt(plaintext.encode()).decode()


def decrypt(cipher: MultiFernet, token: str) -> str:
    return cipher.decrypt(token.encode()).decode()