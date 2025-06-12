# MIT License
#
# Copyright (c) 2025 Dan Stancl
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef, Subquery, Sum
from django.utils import timezone

from api.logging import get_logger
from api.utils import decrypt_data, encrypt_data

logger = get_logger(__name__)


class StravaUser(models.Model):
	"""Represent a user authenticated via Strava."""

	user = models.OneToOneField(
		settings.AUTH_USER_MODEL,
		on_delete=models.CASCADE,
		related_name="strava_profile",
		help_text="Associated Django User",
		null=True,
	)
	strava_id = models.BigIntegerField(
		primary_key=True, unique=True, help_text="Strava Athlete ID"
	)
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
		unique_together = ("user", "activity_type")

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
		unique_together = ("config", "name")
		ordering: ClassVar = ["config", "order", "min_hr"]

	def __str__(self) -> str:
		return f"{self.config}: {self.name} ({self.min_hr}-{self.max_hr} bpm)"

	def clean(self) -> None:
		super().clean()
		if self.min_hr is not None and self.max_hr is not None and self.min_hr > self.max_hr:
			raise ValidationError("Minimum heart rate cannot be greater than maximum heart rate.")


class ZoneSummary(models.Model):
	"""Store aggregated time-in-zone summaries for specific periods."""

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
		current_month_view: int | None = None,
	) -> tuple[ZoneSummary | None, bool]:
		"""Tries to fetch a ZoneSummary.

		If not found or empty, calculates it from ActivityZoneTimes and saves it.
		"""
		summary, created = cls.objects.get_or_create(
			user=user_profile,
			period_type=period_type,
			year=year,
			period_index=period_index,
			defaults={"zone_times_seconds": {}},
		)

		activity_filters = cls._construct_activity_filters(
			user_profile,
			year,
			period_type,
			period_index,
			current_month_view,
		)

		try:
			default_config = CustomZonesConfig.objects.get(
				user=user_profile, activity_type=ActivityType.DEFAULT
			)
		except CustomZonesConfig.DoesNotExist as e:
			raise ValueError("Default CustomZonesConfig not found for user") from e

		time_in_zones = cls._calculate_aggregated_time_in_zones(activity_filters, default_config)

		# Update and save only if newly created or if calculated times differ from stored times
		if created or summary.zone_times_seconds != time_in_zones:
			if not time_in_zones and not created:
				# If calculation results in empty and it wasn't just created
				# (meaning it had data before)
				# and we now have no data for this specific context, ensure we store empty.
				pass  # Handled by assignment below

			summary.zone_times_seconds = time_in_zones
			summary.save()
			logger.info(
				f"ZoneSummary for {user_profile.strava_id}, {period_type}, {year}-{period_index} "
				f"(context: {current_month_view}) updated/created. Data: {time_in_zones}"
			)
		elif not created:
			logger.info(
				f"ZoneSummary for {user_profile.strava_id}, {period_type}, {year}-{period_index} "
				f"(context: {current_month_view}) fetched. "
				f"No change in data: {summary.zone_times_seconds}"
			)

		return summary, created

	@staticmethod
	def _calculate_aggregated_time_in_zones(
		activity_filters: dict[str, int | StravaUser], default_config: CustomZonesConfig
	) -> OrderedDict[str, int]:
		"""Calculate aggregated time in heart rate zones for a given set of activities.

		Returns
		-------
		time_in_zones
			Ordered dictionary of zone names and their aggregated duration in seconds.
		"""
		hr_zone_order_subquery = HeartRateZone.objects.filter(
			config=default_config, name=OuterRef("zone_name")
		).values("order")[:1]

		aggregated_times = (
			ActivityZoneTimes.objects.filter(**activity_filters)
			.values("zone_name")
			.annotate(
				total_duration=Sum("duration_seconds"),
				zone_order=Subquery(hr_zone_order_subquery),
			)
			.order_by("zone_order", "zone_name")
		)

		return OrderedDict(
			{
				item["zone_name"]: item["total_duration"]
				for item in aggregated_times
				if item["total_duration"]
			}
		)

	@staticmethod
	def _construct_activity_filters(
		user_profile: StravaUser,
		year: int,
		period_type: PeriodType,
		period_index: int | None,
		current_month_view: int | None = None,
	) -> dict[str, int | StravaUser]:
		activity_filters = {"user": user_profile, "activity_date__year": year}
		if period_type == ZoneSummary.PeriodType.MONTHLY:
			activity_filters["activity_date__month"] = period_index
		elif period_type == ZoneSummary.PeriodType.WEEKLY:
			activity_filters["activity_date__week"] = period_index
			if current_month_view:  # Apply month context if provided for weekly
				activity_filters["activity_date__month"] = current_month_view
		return activity_filters  # type: ignore[return-value]


class ActivityZoneTimes(models.Model):
	"""Store time spent in each custom heart rate zone for a single activity."""

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


def get_default_processing_start_time() -> datetime:
	"""Return the default start time for activity processing."""
	return timezone.make_aware(datetime(2025, 1, 1)) - timedelta(days=1)


class ActivityProcessingQueue(models.Model):
	"""Queue for processing activities for newly registered users.

	Once fully synced, the user is removed from this queue.
	"""

	user = models.OneToOneField(
		StravaUser,
		on_delete=models.CASCADE,
		related_name="activity_processing_queue",
		primary_key=True,
	)
	last_processed_activity_start_time = models.DateTimeField(
		help_text="The start time of the last successfully processed activity.",
		default=get_default_processing_start_time,
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["updated_at"]
		verbose_name = "Activity Processing Queue"
		verbose_name_plural = "Activity Processing Queues"

	def __str__(self) -> str:
		return f"Queue entry for {self.user.strava_id}"
