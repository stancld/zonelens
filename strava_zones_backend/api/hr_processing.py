from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
	from api.models import CustomZonesConfig

logger = logging.getLogger(__name__)


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
