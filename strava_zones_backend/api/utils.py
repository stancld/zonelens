from __future__ import annotations

import secrets

from cryptography.fernet import Fernet
from django.conf import settings


def get_fernet() -> Fernet:
	"""Initializes Fernet based on the key in settings."""
	key = settings.FERNET_KEY
	if not key:
		raise ValueError("FERNET_KEY not set in settings")
	return Fernet(key)


def encrypt_data(data: str) -> str:
	"""Encrypts string data using Fernet."""
	if not data:
		return ""
	fernet = get_fernet()
	return fernet.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data: str) -> str:
	"""Decrypts string data using Fernet."""
	if not encrypted_data:
		return ""
	fernet = get_fernet()
	return fernet.decrypt(encrypted_data.encode()).decode()


def make_random_password(
	length: int = 10,
	allowed_chars: str = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789",
) -> str:
	return "".join(secrets.choice(allowed_chars) for i in range(length))
