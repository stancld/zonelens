from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone

from api.hr_processing import OUTSIDE_ZONES_KEY, calculate_time_in_zones, parse_activity_streams
from api.logging import get_logger
from api.models import (
	ActivityType,
	ActivityZoneTimes,
	CustomZonesConfig,
	HeartRateZone,
	StravaUser,
)
from api.strava_client import StravaApiClient

if TYPE_CHECKING:
	from typing import Any


class StravaHRWorker:
	"""
	Worker to fetch Strava activities for a user, process heart rate data,
	and store time-in-zone information.
	"""

	def __init__(self, user_strava_id: int):
		try:
			self.user = StravaUser.objects.get(strava_id=user_strava_id)
		except StravaUser.DoesNotExist as e:
			raise ValueError(f"User with Strava ID {user_strava_id} not found.") from e

		# Initialize Strava API client
		# This assumes StravaApiClient takes a StravaUser object for authentication
		self.strava_client = StravaApiClient(self.user)
		self.logger = get_logger(__name__)

	def _get_default_zones_config(self) -> CustomZonesConfig | None:
		"""
		Retrieves the default CustomZonesConfig for the user.
		Logs a warning if no default config or no zones are defined within it.
		"""
		try:
			# Attempt to get the default configuration for the user
			config = CustomZonesConfig.objects.get(
				user=self.user, activity_type=ActivityType.DEFAULT
			)
			if not config.zones_definition.exists():
				self.logger.warning(
					f"Default CustomZonesConfig {config.id} for user {self.user.strava_id} "
					"has no zone definitions. Will treat all time as 'outside zones'."
				)
				# We can still proceed, calculate_time_in_zones will handle this
			return config
		except CustomZonesConfig.DoesNotExist:
			self.logger.warning(
				f"No default CustomZonesConfig found for user {self.user.strava_id}. "
				"Cannot process HR data without zone definitions."
			)
			return None
		except CustomZonesConfig.MultipleObjectsReturned:
			self.logger.warning(
				f"Multiple default CustomZonesConfigs found for user {self.user.strava_id}. "
				"Using the first one. Please ensure only one default config per user."
			)
			config = CustomZonesConfig.objects.filter(
				user=self.user, activity_type=ActivityType.DEFAULT
			).first()
			if config and not config.zones_definition.exists():
				self.logger.warning(
					f"Selected CustomZonesConfig {config.id} for user {self.user.strava_id} "
					"has no zone definitions."
				)
			return config

	def process_user_activities(self, after_timestamp: int | None = None) -> None:  # noqa: C901
		"""Process user activities.

		Fetches user's Strava activities after a given unix timestamp,
		processes heart rate data, and stores results in ActivityZoneTimes.
		"""
		self.logger.info(f"Starting activity processing for user {self.user.strava_id}.")

		zones_config = self._get_default_zones_config()
		if not zones_config:
			raise ValueError(f"Zones config not found for user {self.user.strava_id}.")

		try:
			activities = self.strava_client.fetch_strava_activities(after=after_timestamp)
		except Exception as e:
			raise ValueError(f"Failed to fetch activities for user {self.user.strava_id}") from e

		if not activities:
			self.logger.info(f"No new activities found for user {self.user.strava_id} to process.")
			return

		processed_count = 0
		for activity_summary in activities:
			if (activity_id_str := activity_summary.get("id")) is None:
				self.logger.warning("Activity summary missing ID. Skipping.")
				continue
			activity_id = int(activity_id_str)
			activity_date_attr = activity_summary.get("start_date")

			activity_date = self._parse_activity_date(activity_date_attr)

			# Ensure activity_date is timezone-aware if it's naive
			if timezone.is_naive(activity_date):
				activity_date = timezone.make_aware(activity_date, timezone.utc)
			has_hr = activity_summary.get("has_heartrate", False)

			if not has_hr:
				self.logger.info(
					f"Activity {activity_id} for user {self.user.strava_id} "
					f"(date: {activity_date.date()}) has no heart rate data. Skipping."
				)
				continue

			try:
				streams_data = self.strava_client.fetch_activity_streams(activity_id=activity_id)
			except Exception as e:
				self.logger.error(
					f"Failed to fetch or parse streams for activity {activity_id} "
					f"(user {self.user.strava_id}): {e}"
				)
				continue

			time_data, hr_data = parse_activity_streams(streams_data)
			if not time_data or not hr_data:
				continue

			zone_times_dict = calculate_time_in_zones(time_data, hr_data, zones_config)
			if time_outside_zone := zone_times_dict.pop(OUTSIDE_ZONES_KEY, 0):
				self.logger.warning(
					f"There is {time_outside_zone} s outside any zone for activity {activity_id}."
				)
			for zone_name, duration_seconds in zone_times_dict.items():
				if duration_seconds > 0:  # Only store if time was spent in the zone
					_obj, _created = ActivityZoneTimes.objects.update_or_create(
						user=self.user,
						activity_id=activity_id,
						zone_name=zone_name,
						defaults={
							"duration_seconds": duration_seconds,
							"activity_date": activity_date,
						},
					)
			processed_count += 1
			self.logger.info(
				f"[SUCESS] Activity {activity_id} for user {self.user.strava_id} "
				f"(date: {activity_date.date()})."
			)

		self.logger.info(
			f"Finished processing for user {self.user.strava_id}. "
			f"Processed {processed_count} activities with HR data."
		)

	@staticmethod
	def _parse_activity_date(
		activity_date_attr: str | timezone.datetime | Any,
	) -> timezone.datetime:
		if isinstance(activity_date_attr, str):
			return timezone.datetime.fromisoformat(activity_date_attr.replace("Z", "+00:00"))
		return activity_date_attr

	def fetch_and_store_strava_hr_zones(self) -> bool:
		"""Fetches HR zones from Strava and stores them in the database.

		Returns
		-------
		    True if zones were successfully fetched and stored, False otherwise.
		"""
		self.logger.info(f"Attempting to fetch Strava HR zones for user {self.user.strava_id}.")
		try:
			strava_zones_data = self.strava_client.fetch_athlete_zones()
			if not strava_zones_data:
				self.logger.warning(
					f"No zone data returned from Strava for user {self.user.strava_id}."
				)
				return False

			heart_rate_zones_data = strava_zones_data["heart_rate"]["zones"]
			# Ensure the default CustomZonesConfig exists for this user and activity type
			config, config_created = CustomZonesConfig.objects.update_or_create(
				user=self.user,
				activity_type=ActivityType.DEFAULT,
			)
			# Always clear existing zones for this config before potentially adding new ones
			# This handles both updating with new zones and clearing out old ones
			num_deleted, _ = HeartRateZone.objects.filter(config=config).delete()
			if num_deleted > 0:
				self.logger.info(
					f"Deleted {num_deleted} old HR zones for config {config.id} "
					f"for user {self.user.strava_id}."
				)

			if not heart_rate_zones_data:
				self.logger.info(
					f"No HR zones found on Strava for user {self.user.strava_id}. "
					f"Any existing zones for default config {config.id} have been cleared."
				)
				return True  # Operation successful, state reflects no zones

			# Strava returned HR zones, so create them
			new_zones_to_create = []
			for i, zone_data in enumerate(heart_rate_zones_data):
				min_hr = zone_data.get("min")
				max_hr_strava = zone_data.get(
					"max"
				)  # Renamed to avoid clash with model field name

				# Strava's last zone has max as -1, interpret as no upper limit
				# PositiveIntegerField in model typically cannot be None and needs a value.
				max_hr_db = 220 if max_hr_strava == -1 or max_hr_strava is None else max_hr_strava

				# Assuming min_hr is always present and valid from Strava.
				# If min_hr could be missing, handle similarly.
				# min_hr_db = min_hr if min_hr is not None else 0 # Example

				new_zones_to_create.append(
					HeartRateZone(
						config=config,
						name=f"Z{i + 1}",  # Strava zones are typically Z1-Z5
						order=i + 1,
						min_hr=min_hr,  # Assumes min_hr is always present from Strava
						max_hr=max_hr_db,
					)
				)

			if new_zones_to_create:
				HeartRateZone.objects.bulk_create(new_zones_to_create)
			else:  # Should ideally not happen if heart_rate_zones_data is not empty
				self.logger.info("Strava returned zone data, but no new HR zones were processed.")

			return True

		except Exception as e:
			self.logger.exception(
				f"Error fetching/storing Strava HR zones for user {self.user.strava_id}: {e}"
			)
			return False
