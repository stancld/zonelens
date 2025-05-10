from __future__ import annotations

import uuid
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from api.logging import get_logger
from api.utils import decrypt_data, encrypt_data

logger = get_logger(__name__)


class StravaUser(models.Model):
	"""Represents a user authenticated via Strava."""

	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="strava_profile",
		help_text="Associated Django User",
		null=True,  # TODO: Generate new migration and enforce not-null
	)
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

	@classmethod
	def get_primary_key_field_name(cls) -> str:
		return "strava_id"

	@property
	def access_token(self) -> str | None:
		return decrypt_data(self._access_token)

	@access_token.setter
	def access_token(self, value: str) -> None:
		self._access_token = encrypt_data(value)

	@property
	def refresh_token(self) -> str | None:
		return decrypt_data(self._refresh_token)

	@refresh_token.setter
	def refresh_token(self, value: str) -> None:
		self._refresh_token = encrypt_data(value)


class ActivityType(models.TextChoices):
	DEFAULT = "DEFAULT", "Default"
	RUN = "RUN", "Run"
	RIDE = "RIDE", "Ride"


class CustomZonesConfig(models.Model):
	"""Configuration grouping for a user's HR zones for a specific activity type."""

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
	"""Stores aggregated time-in-zone summaries for specific periods."""

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
		help_text="JSON containing aggregated time in seconds for each zone name for the period.",
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("user", "period_type", "year", "period_index")
		indexes: ClassVar = [models.Index(fields=["user", "period_type", "year", "period_index"])]
		verbose_name = "Periodic Zone Summary"
		verbose_name_plural = "Periodic Zone Summaries"

	def __str__(self) -> str:
		return f"{self.get_period_type_display()} summary for {self.user.strava_id} - {self.year}/{self.period_index}"  # noqa: E501

	@classmethod
	def get_or_create_summary(
		cls,
		*,
		user_profile: StravaUser,
		period_type: PeriodType,
		year: int,
		period_index: int | None = None,
		current_month_view: int | None = None,  # Month being viewed, for context
	) -> tuple[ZoneSummary | None, bool]:
		"""Tries to fetch a ZoneSummary.

		If not found or empty, calculates it from ActivityZoneTimes and saves it.
		"""
		summary, created = cls.objects.get_or_create(
			user=user_profile,
			period_type=period_type,
			year=year,
			period_index=period_index,
			defaults={"zone_times_seconds": {}},  # Default to empty if we need to create
		)

		# Always determine activity filters based on current parameters
		activity_filters = {"user": user_profile, "activity_date__year": year}
		if period_type == ZoneSummary.PeriodType.MONTHLY:
			activity_filters["activity_date__month"] = period_index
		elif period_type == ZoneSummary.PeriodType.WEEKLY:
			activity_filters["activity_date__week"] = period_index
			if current_month_view:  # Apply month context if provided for weekly
				activity_filters["activity_date__month"] = current_month_view

		# Always calculate what the zone times should be based on these filters
		aggregated_times = (
			ActivityZoneTimes.objects.filter(**activity_filters)
			.values("zone_name")
			.annotate(total_duration=Sum("duration_seconds"))
			.order_by("zone_name")
		)
		calculated_zone_times = {
			item["zone_name"]: item["total_duration"]
			for item in aggregated_times
			if item["total_duration"] and item["total_duration"] > 0
		}

		# Update and save only if newly created or if calculated times differ from stored times
		if created or summary.zone_times_seconds != calculated_zone_times:
			if not calculated_zone_times and not created:
				# If calculation results in empty and it wasn't just created
				# (meaning it had data before)
				# and we now have no data for this specific context, ensure we store empty.
				pass  # Handled by assignment below

			summary.zone_times_seconds = calculated_zone_times
			summary.save()
			logger.info(
				f"ZoneSummary for {user_profile.strava_id}, {period_type}, {year}-{period_index} "
				f"(context: {current_month_view}) updated/created. Data: {calculated_zone_times}"
			)
		elif not created:
			logger.info(
				f"ZoneSummary for {user_profile.strava_id}, {period_type}, {year}-{period_index} "
				f"(context: {current_month_view}) fetched. "
				f"No change in data: {summary.zone_times_seconds}"
			)

		return summary, created


class ActivityZoneTimes(models.Model):
	"""Stores time spent in each custom heart rate zone for a single activity."""

	id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
	user = models.ForeignKey(
		StravaUser, on_delete=models.CASCADE, related_name="activity_zone_times"
	)
	activity_id = models.BigIntegerField(help_text="Strava Activity ID", db_index=True)
	zone_name = models.CharField(max_length=100, help_text="Custom name of the heart rate zone")
	duration_seconds = models.PositiveIntegerField(
		help_text="Time spent in this zone for this activity in seconds"
	)
	activity_date = models.DateTimeField(
		help_text="Date and time the activity started", default=timezone.now
	)  # Added for sorting/filtering
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("user", "activity_id", "zone_name")
		ordering = ["-activity_date", "user", "zone_name"]
		indexes: ClassVar = [
			models.Index(fields=["user", "activity_id"]),
			models.Index(fields=["user", "activity_date"]),
		]
		verbose_name = "Activity Zone Time"
		verbose_name_plural = "Activity Zone Times"

	def __str__(self) -> str:
		return (
			f"{self.user.strava_id} - "
			f"Activity {self.activity_id}: {self.zone_name} ({self.duration_seconds}s)"
		)
