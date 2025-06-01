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


DEFAULT_ZONES_NAMES_MAPPING = {
	1: "Recovery (Easy)",
	2: "Endurance (Easy)",
	3: "Tempo",
	4: "Threshold",
	5: "Anaerobic",
}


class Worker:
	"""Fetch activities for a user, process heart rate data and store time-in-zone information."""

	def __init__(self, user_strava_id: int):
		try:
			self.user = StravaUser.objects.get(strava_id=user_strava_id)
		except StravaUser.DoesNotExist as e:
			raise ValueError(f"User with Strava ID {user_strava_id} not found.") from e

		self.strava_client = StravaApiClient(self.user)
		self.logger = get_logger(__name__)

	def process_user_activities(self, after_timestamp: int | None = None) -> None:  # noqa: C901
		"""Process user activities in bulk after the account setup.

		Fetches user's Strava activities after a given unix timestamp,
		processes heart rate data, and stores results in ActivityZoneTimes.
		Uses activity-specific HR zone configurations if available, otherwise DEFAULT.
		"""
		self.logger.info(f"Starting activity processing for user {self.user.strava_id}.")

		all_zone_configs = self._get_all_user_zone_configs()

		if not (default_zones_config := all_zone_configs.get(ActivityType.DEFAULT)):  # type: ignore[call-overload]
			self.logger.error(
				f"Default zones config not found or obtainable for user {self.user.strava_id}."
			)
			raise ValueError(f"Default zones config not found for user {self.user.strava_id}.")

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

			if not activity_summary.get("has_heartrate", False):
				self.logger.info(
					f"Activity {activity_id} for user {self.user.strava_id} "
					f"(date: {activity_date.date()}) has no heart rate data. Skipping."
				)
				continue

			# Determine which zone configuration to use
			strava_activity_type_str: str | None = activity_summary.get("type")
			target_config_type = self._map_strava_activity_to_config_type(strava_activity_type_str)

			selected_zones_config = all_zone_configs.get(target_config_type, default_zones_config)

			self.logger.info(
				f"Processing activity {activity_id} (Strava type: {strava_activity_type_str}, "
				f"Mapped type: {target_config_type.label}) using "
				f"'{selected_zones_config.get_activity_type_display()}' config."
			)

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

			zone_times_dict = calculate_time_in_zones(time_data, hr_data, selected_zones_config)
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

	def process_new_activity(self, user_strava_id: int, activity_id: int) -> None:
		"""Process a single new activity for a given user upon a webhook event notification."""
		self.logger.info(f"Starting processing activity {activity_id} for user {user_strava_id}.")

		if not (activity_summary := self.strava_client.fetch_activity_details(activity_id)):
			raise ValueError(f"Failed to fetch details for activity {activity_id}.")

		activity_date = self._parse_activity_date(activity_summary.get("start_date"))
		if timezone.is_naive(activity_date):
			activity_date = timezone.make_aware(activity_date, timezone.utc)

		if not activity_summary.get("has_heartrate", False):
			self.logger.info(f"Activity {activity_id} has no heart rate data. Skipping.")
			return

		all_zone_configs = self._get_all_user_zone_configs()
		if not (default_zones_config := all_zone_configs.get(ActivityType.DEFAULT)):  # type: ignore[call-overload]
			raise ValueError(f"Default zones config not found for user {user_strava_id}.")

		target_config_type = self._map_strava_activity_to_config_type(activity_summary.get("type"))
		selected_zones_config = all_zone_configs.get(target_config_type, default_zones_config)

		if not (streams_data := self.strava_client.fetch_activity_streams(activity_id)):
			raise ValueError(f"Failed to fetch stream for activity {activity_id}.")

		time_data, hr_data = parse_activity_streams(streams_data)
		zone_times_dict = calculate_time_in_zones(time_data, hr_data, selected_zones_config)

		if time_outside_zone := zone_times_dict.pop(OUTSIDE_ZONES_KEY, 0):
			self.logger.warning(
				f"There is {time_outside_zone} s outside any zone for activity {activity_id}."
			)

		for zone_name, duration_seconds in zone_times_dict.items():
			if duration_seconds > 0:
				ActivityZoneTimes.objects.create(
					user=self.user,
					activity_id=activity_id,
					zone_name=zone_name,
					duration_seconds=duration_seconds,
					activity_date=activity_date,
				)

		self.logger.info(f"Successfully processed activity {activity_id}.")

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
				return True

			# Strava returned HR zones, so create them
			new_zones_to_create = []
			for idx, zone_data in enumerate(heart_rate_zones_data, start=1):
				min_hr = zone_data.get("min")
				max_hr_strava = zone_data.get("max")

				# Strava's last zone has max as -1, interpret as no upper limit
				# PositiveIntegerField in model typically cannot be None and needs a value.
				max_hr_db = 220 if max_hr_strava == -1 or max_hr_strava is None else max_hr_strava

				new_zones_to_create.append(
					HeartRateZone(
						config=config,
						name=DEFAULT_ZONES_NAMES_MAPPING[idx],
						order=idx,
						min_hr=min_hr,
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

	def _get_default_zones_config(self) -> CustomZonesConfig | None:
		try:
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

	def _get_all_user_zone_configs(self) -> dict[ActivityType, CustomZonesConfig]:
		"""Fetches all zone configurations for the user and organizes them by ActivityType."""
		user_configs = CustomZonesConfig.objects.filter(user=self.user).prefetch_related(
			"zones_definition"
		)

		configs_map: dict[ActivityType, CustomZonesConfig] = {}
		has_explicit_default = False
		for config in user_configs:
			try:
				activity_type_enum = ActivityType(config.activity_type)
				configs_map[activity_type_enum] = config
				if activity_type_enum == ActivityType.DEFAULT:
					has_explicit_default = True
					if not config.zones_definition.exists():
						self.logger.warning(
							f"Explicit default CustomZonesConfig {config.id} for user {self.user.strava_id} "  # noqa: E501
							"has no zone definitions. Time will be 'outside zones'."
						)
			except ValueError:
				self.logger.error(
					f"Invalid activity_type '{config.activity_type}' in DB for CustomZonesConfig {config.id} "  # noqa: E501
					f"for user {self.user.strava_id}. Skipping this config."
				)

		if not has_explicit_default:
			self.logger.info(
				f"No explicit DEFAULT config in DB for user {self.user.strava_id}. "
				"Checking via _get_default_zones_config."
			)
			default_config_from_method = self._get_default_zones_config()
			if default_config_from_method:
				configs_map[ActivityType.DEFAULT] = default_config_from_method  # type: ignore[index]
			# If default_config_from_method is None, process_user_activities will handle it.

		return configs_map

	def _map_strava_activity_to_config_type(
		self, strava_activity_type_str: str | None
	) -> ActivityType:
		"""Maps a Strava activity type string to our internal ActivityType enum."""
		if not strava_activity_type_str:
			return ActivityType.DEFAULT  # type: ignore[return-value]

		# Common Strava activity types: https://developers.strava.com/docs/reference/#api-models-ActivityType
		# We are simplifying to RUN, RIDE, or DEFAULT
		# Note: Case-sensitive matching as per Strava's typical API responses.
		RUN_TYPES = {"Run", "VirtualRun", "TrailRun"}
		RIDE_TYPES = {"Ride", "VirtualRide", "EBikeRide", "Handcycle", "Velomobile"}

		if strava_activity_type_str in RUN_TYPES:
			return ActivityType.RUN  # type: ignore[return-value]
		if strava_activity_type_str in RIDE_TYPES:
			return ActivityType.RIDE  # type: ignore[return-value]

		# For other types like Swim, AlpineSki, Kayaking, etc., use default.
		return ActivityType.DEFAULT  # type: ignore[return-value]

	@staticmethod
	def _parse_activity_date(
		activity_date_attr: str | timezone.datetime | Any,
	) -> timezone.datetime:
		if isinstance(activity_date_attr, str):
			return timezone.datetime.fromisoformat(activity_date_attr.replace("Z", "+00:00"))
		return activity_date_attr
