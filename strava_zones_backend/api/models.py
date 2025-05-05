from __future__ import annotations

import logging
import uuid
from typing import ClassVar

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError  # For model clean method
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- Encryption functions (encrypt_data, decrypt_data) ---
# Ensure FERNET_KEY is configured in settings
try:
	# Ensure the key is bytes
	fernet_key = (
		settings.FERNET_KEY.encode()
		if isinstance(settings.FERNET_KEY, str)
		else settings.FERNET_KEY
	)
	if not fernet_key:
		raise ValueError("FERNET_KEY is not set in Django settings.")
	cipher_suite = Fernet(fernet_key)
except (AttributeError, ValueError) as e:
	logger.error(f"Fernet key configuration error: {e}. Encryption/decryption will fail.")
	# Fallback or raise error depending on requirements during startup
	cipher_suite = None


def encrypt_data(data) -> str | None:
	"""Encrypts data using the Fernet cipher suite."""
	if cipher_suite is None:
		raise ValueError("Encryption cipher suite not available.")
	if data is None:
		return None
	# Ensure data is bytes
	data_bytes = data.encode() if isinstance(data, str) else data
	encrypted_data = cipher_suite.encrypt(data_bytes)
	return encrypted_data.decode()  # Store as string in db


def decrypt_data(encrypted_data):
	"""Decrypts data using the Fernet cipher suite."""
	if cipher_suite is None:
		raise ValueError("Decryption cipher suite not available.")
	if encrypted_data is None:
		return None
	encrypted_bytes = (
		encrypted_data.encode() if isinstance(encrypted_data, str) else encrypted_data
	)
	decrypted_data = cipher_suite.decrypt(encrypted_bytes)
	return decrypted_data.decode()


class StravaUser(models.Model):
	"""Represents a user authenticated via Strava."""

	strava_id = models.BigIntegerField(
		primary_key=True, unique=True, help_text="Strava Athlete ID"
	)
	# Store encrypted tokens as text. Use properties for easy access.
	_access_token = models.TextField(
		default="", blank=True, help_text="Encrypted Strava access token"
	)
	_refresh_token = models.TextField(
		default="", blank=True, help_text="Encrypted Strava refresh token"
	)
	token_expires_at = models.DateTimeField(
		default=None, blank=True, help_text="Expiry timestamp for the access token"
	)
	scope = models.CharField(
		max_length=255, default="", blank=True, help_text="Permissions scope granted by user"
	)
	last_login = models.DateTimeField(default=timezone.now)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	def __str__(self) -> str:
		return f"User {self.strava_id}"

	@property
	def access_token(self) -> str:
		return decrypt_data(self._access_token)

	@access_token.setter
	def access_token(self, value: str) -> None:
		self._access_token = encrypt_data(value)

	@property
	def refresh_token(self) -> str:
		return decrypt_data(self._refresh_token)

	@refresh_token.setter
	def refresh_token(self, value: str) -> None:
		self._refresh_token = encrypt_data(value)


class CustomZonesConfig(models.Model):
	"""Configuration grouping for a user's HR zones for a specific activity type."""

	class ActivityType(models.TextChoices):
		DEFAULT = "DEFAULT", "Default"
		RUN = "RUN", "Run"
		RIDE = "RIDE", "Ride"

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	user = models.ForeignKey(StravaUser, on_delete=models.CASCADE, related_name="zone_configs")
	activity_type = models.CharField(
		max_length=20, choices=ActivityType.choices, default=ActivityType.DEFAULT
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("user", "activity_type")  # Ensure one config type per user

	def __str__(self):
		return f"Zone Config for {self.user.strava_id} - {self.get_activity_type_display()}"


class HeartRateZone(models.Model):
	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	config = models.ForeignKey(
		CustomZonesConfig, on_delete=models.CASCADE, related_name="zones_definition"
	)
	name = models.CharField(max_length=50, help_text="e.g., 'Zone 1', 'Recovery'")
	min_hr = models.PositiveIntegerField(help_text="Minimum heart rate for this zone (inclusive)")
	max_hr = models.PositiveIntegerField(help_text="Maximum heart rate for this zone (inclusive)")
	order = models.PositiveSmallIntegerField(
		default=0, help_text="Order of the zone (e.g., 1, 2, 3...)"
	)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("config", "name")  # Zone names should be unique within a config
		ordering: ClassVar = ["config", "order", "min_hr"]

	def __str__(self) -> str:
		return f"{self.config}: {self.name} ({self.min_hr}-{self.max_hr} bpm)"

	def clean(self) -> None:
		super().clean()
		if self.min_hr is not None and self.max_hr is not None and self.min_hr > self.max_hr:
			raise ValidationError("Minimum heart rate cannot be greater than maximum heart rate.")


class ZoneSummary(models.Model):
	"""Stores aggregated time-in-zone summaries."""

	class PeriodType(models.TextChoices):
		WEEKLY = "WEEKLY", "Weekly"
		MONTHLY = "MONTHLY", "Monthly"

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	user = models.ForeignKey(StravaUser, on_delete=models.CASCADE, related_name="zone_summaries")
	period_type = models.CharField(max_length=10, choices=PeriodType.choices)
	year = models.PositiveIntegerField()
	period_index = models.PositiveIntegerField(help_text="Month (1-12) or Week (1-53)")
	zone_times_seconds = models.JSONField(
		default=dict,
		help_text="JSON containing aggregated time in seconds for each zone name.",
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("user", "period_type", "year", "period_index")
		indexes: ClassVar = [models.Index(fields=["user", "period_type", "year", "period_index"])]

	def __str__(self) -> str:
		return f"{self.get_period_type_display()} summary for {self.user.strava_id} - {self.year}/{self.period_index}"  # noqa: E501
