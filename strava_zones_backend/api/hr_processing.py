from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api.logging import get_logger

if TYPE_CHECKING:
	from api.models import CustomZonesConfig

logger = get_logger(__name__)

OUTSIDE_ZONES_KEY = "Time Outside Defined Zones"


def parse_activity_streams(
	streams_data: dict[str, Any] | None,
) -> tuple[list[int] | None, list[int] | None]:
	"""
	Parses the raw activity stream data from Strava to extract time and heart rate series.

	Parameters
	----------
	streams_data
	    A dictionary representing the JSON response from the Strava API's
	    getLoggedInAthleteActivityStreams endpoint, keyed by stream type.
	    Expected to contain 'time' and 'heartrate' streams.

	Returns
	-------
	    A tuple (time_data, heartrate_data).
	    - time_data: A list of integers representing the time series in seconds,
	                 or None if not found or invalid.
	    - heartrate_data: A list of integers representing the heart rate series in bpm,
	                      or None if not found or invalid.
	"""
	if not streams_data:
		logger.warning("No stream data provided to parse.")
		return None, None

	time_stream = streams_data.get("time")
	heartrate_stream = streams_data.get("heartrate")

	time_data: list[int] | None = None
	heartrate_data: list[int] | None = None

	if isinstance(time_stream, dict) and isinstance(time_stream.get("data"), list):
		time_data = time_stream["data"]
		if not time_data:  # Handle empty list case
			logger.warning("Time stream data array is empty.")
			time_data = None
		elif not all(isinstance(t, int) for t in time_data):
			logger.warning("Time stream data contains non-integer values.")
			time_data = None
	else:
		logger.warning("Time stream not found or data is not a list.")

	if isinstance(heartrate_stream, dict) and isinstance(heartrate_stream.get("data"), list):
		heartrate_data = heartrate_stream["data"]
		if not heartrate_data:  # Handle empty list case
			logger.warning("Heartrate stream data array is empty.")
			heartrate_data = None
		elif not all(isinstance(hr, int) for hr in heartrate_data):
			logger.warning("Heartrate stream data contains non-integer values.")
			heartrate_data = None
	else:
		logger.warning("Heartrate stream not found or data is not a list.")

	if time_data and heartrate_data and len(time_data) != len(heartrate_data):
		logger.warning(
			f"Time stream (len {len(time_data)}) and heartrate stream (len {len(heartrate_data)}) "
			"have different lengths. This might indicate an issue with the data."
		)

	return time_data, heartrate_data


def determine_hr_zone(hr_value: int, zones_config: CustomZonesConfig) -> str | None:
	"""
	Determines the custom heart rate zone for a given heart rate value.

	Returns
	-------
	The name of the heart rate zone (e.g., "Zone 1", "Zone 2") or None
	if the hr_value does not fall into any defined zone.
	Assumes zones are ordered by name or that iteration order is acceptable.
	If zones can overlap, the first matching zone encountered will be returned.
	"""
	if not zones_config:
		logger.warning("determine_hr_zone called with no zones_config object.")
		return None

	try:
		# Fetch all related HeartRateZone objects, ordered by min_hr
		# The model's Meta.ordering might also apply, but explicit sort here is clearer for function's logic.  # noqa: E501
		# Sorting by 'order' first could be an option if 'order' is reliably managed and intended for primary sort.  # noqa: E501
		# For now, min_hr is the crucial sorting key for this function's logic.
		all_zones = list(zones_config.zones_definition.order_by("min_hr"))

	except Exception as e:  # Catch potential errors during DB query or related manager access
		err_msg = (
			f"Error accessing or sorting zones for user {zones_config.user_id}, "
			f"activity type {zones_config.activity_type}. Error: {e}"
		)
		logger.error(err_msg)
		return None

	if not all_zones:
		logger.debug(
			f"No heart rate zones defined for user {zones_config.user_id}, "
			f"activity type {zones_config.activity_type}."
		)
		return None

	for zone in all_zones:
		if not isinstance(zone.min_hr, int) or not isinstance(zone.max_hr, int):
			continue
		if zone.min_hr > zone.max_hr:
			continue
		if zone.min_hr <= hr_value <= zone.max_hr:
			return zone.name

	# Construct message first to manage line length
	defined_zones_str = ", ".join([f"{z.name}: {z.min_hr}-{z.max_hr}" for z in all_zones])
	debug_msg = (
		f"HR value {hr_value} is out of defined zones for user {zones_config.user_id}, "
		f"activity type {zones_config.activity_type}. Defined zones: [{defined_zones_str}]"
	)
	logger.debug(debug_msg)
	return None  # HR value is outside all defined zones


def calculate_time_in_zones(
	time_data: list[int] | None,
	heartrate_data: list[int] | None,
	zones_config: CustomZonesConfig | None,
) -> dict[str, int]:
	"""
	Calculates the total time spent in each custom heart rate zone for an activity.

	Parameters
	----------
	time_data
	    A list of integers representing the time series in seconds (sorted).
	heartrate_data
	    A list of integers representing the heart rate series in bpm.
	zones_config
	    The CustomZonesConfig object containing the zone definitions.

	Returns
	-------
	    A dictionary where keys are zone names (str) and values are total time
	    spent in that zone in seconds (int). Includes a key for time spent
	    outside any defined zones.
	"""
	time_spent_in_zones: dict[str, int] = {OUTSIDE_ZONES_KEY: 0}

	if not zones_config:
		logger.warning(
			"calculate_time_in_zones called with no zones_config. Times will be 'outside zones'."
		)
		# If no zones_config, all time is technically 'outside' but we need durations.
		# Fall through, determine_hr_zone will return None for all HRs.
	else:
		try:
			for zone_model in zones_config.zones_definition.all():
				time_spent_in_zones[zone_model.name] = 0
		except Exception as e:  # Handle DB error if zones_definition can't be accessed
			logger.error(
				f"Error accessing zone definitions for config {zones_config.id}: {e}. "
				"Proceeding as if no zones were defined."
			)
			# Reset to just OUTSIDE_ZONES_KEY if there was an error fetching actual zones
			time_spent_in_zones = {OUTSIDE_ZONES_KEY: 0}

	if not time_data or not heartrate_data:
		logger.warning("Time or HR data is missing. Cannot calculate time in zones.")
		return time_spent_in_zones  # Return initialized (mostly empty) dict

	if len(time_data) != len(heartrate_data):
		logger.warning(
			"Time and HR data lists have different lengths. "
			"Cannot accurately calculate time in zones."
		)
		return time_spent_in_zones  # Return initialized dict

	if len(time_data) < 2:
		logger.info("Insufficient data points (need at least 2) to calculate time in zones.")
		return time_spent_in_zones  # No durations to calculate

	for i in range(len(time_data) - 1):
		hr_value = heartrate_data[i]
		duration_seconds = time_data[i + 1] - time_data[i]

		if duration_seconds <= 0:
			continue

		zone_name = determine_hr_zone(hr_value, zones_config)  # type: ignore[arg-type]

		if zone_name:
			time_spent_in_zones[zone_name] = (
				time_spent_in_zones.get(zone_name, 0) + duration_seconds
			)
		else:
			time_spent_in_zones[OUTSIDE_ZONES_KEY] += duration_seconds

	return time_spent_in_zones
