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

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from api.logging import get_logger
from api.models import ActivityType

if TYPE_CHECKING:
	from collections.abc import Sequence

	from api.models import CustomZonesConfig

logger = get_logger(__name__)

OUTSIDE_ZONES_KEY = "Time Outside Defined Zones"
# Kind of arbitrary constant to match Strava calculation as much as possible
# Strava algorithm is for sure different, but this is our nice poor man's solution
MOVING_DISTANCE_THRESHOLDS = defaultdict(
	lambda: 0.8,
	{
		ActivityType.RIDE: 3.0,
		ActivityType.RUN: 2.0,
		ActivityType.DEFAULT: 0.8,
	},
)


def parse_activity_streams(
	streams_data: dict[str, Any] | None,
) -> tuple[list[int] | None, list[int] | None, list[float] | None, list[bool] | None]:
	"""Parse the raw activity stream data from Strava to extract time and heart rate series.

	Parameters
	----------
	streams_data
	    A dictionary representing the JSON response from the Strava API's
	    getLoggedInAthleteActivityStreams endpoint, keyed by stream type.

	Returns
	-------
	time_data
		Time series in seconds, or None if not found or invalid.
	heartrate_data
		Heart rate series in bpm, or None if not found or invalid.
	distance_data
		Distance series in meters, or None if not found or invalid.
	moving_data
		Moving data series, or None if not found or invalid.
	"""
	if not streams_data:
		logger.warning("No stream data provided to parse.")
		return None, None, None, None

	time_data = _parse_activity_stream(streams_data, "time")
	heartrate_data = _parse_activity_stream(streams_data, "heartrate")
	distance_data = _parse_activity_stream(streams_data, "distance", float)
	moving_data = _parse_activity_stream(streams_data, "moving", bool)

	return time_data, heartrate_data, distance_data, moving_data  # type: ignore[return-value]


def _parse_activity_stream(
	data_streams: dict[str, Any], stream_type: str, expected_type: type[bool | int | float] = int
) -> list[int] | list[bool] | list[float] | None:
	stream = data_streams.get(stream_type)
	if isinstance(stream, dict) and isinstance(stream.get("data"), list):
		if not (data := stream["data"]):
			logger.warning(f"{stream_type.capitalize()} stream data array is empty.")
			return None
		if not all(isinstance(t, expected_type) for t in data):
			logger.warning(f"{stream_type.capitalize()} stream data contains non-integer values.")
			return None
		return data

	logger.warning(f"{stream_type.capitalize()} stream not found or data is not a list.")
	return None


def determine_hr_zone(hr_value: int, zones_config: CustomZonesConfig | None) -> str | None:
	"""Determine the custom heart rate zone for a given heart rate value.

	Returns
	-------
	The name of the heart rate zone (e.g., "Zone 1", "Zone 2")
	or None if the hr_value does not fall into any defined zone.
	"""
	if not zones_config:
		logger.warning("determine_hr_zone called with no zones_config object.")
		return None

	try:
		all_zones = list(zones_config.zones_definition.order_by("min_hr"))
	except Exception as e:
		err_msg = (
			f"Error accessing or sorting zones for user {zones_config.user_id}, "
			f"activity type {zones_config.activity_type}. Error: {e}"
		)
		logger.error(err_msg)
		return None

	if not all_zones:
		logger.warning(
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

	# HR value is outside all defined zones
	return None


def calculate_time_in_zones(
	time_data: list[int] | None,
	heartrate_data: list[int] | None,
	distance_data: list[float] | None,
	moving_data: list[bool] | None,
	zones_config: CustomZonesConfig | None,
) -> dict[str, int]:
	"""Calculate the total time spent in each custom heart rate zone for an activity.

	Parameters
	----------
	time_data
	    A list of integers representing the time series in seconds (sorted).
	heartrate_data
	    A list of integers representing the heart rate series in bpm.
	distance_data
		A list of floats representing the distance series in meters.
	moving_data
	    A list of booleans representing whether the activity was moving at each time point.
	zones_config
	    The CustomZonesConfig object containing the zone definitions.

	Returns
	-------
	Time in zones
	    A dictionary where keys are zone names and values are total time spent in that zone
		in seconds. Includes a key for time spent outside any defined zones.
	"""
	time_spent_in_zones: dict[str, int] = {OUTSIDE_ZONES_KEY: 0}

	if not zones_config:
		logger.warning(
			"calculate_time_in_zones called with no zones_config. Times will be 'outside zones'."
		)
	else:
		try:
			for zone_model in zones_config.zones_definition.all().order_by("order"):
				time_spent_in_zones[zone_model.name] = 0
		except Exception as e:
			logger.error(
				f"Error accessing zone definitions for config {zones_config.id}: {e}. "
				"Proceeding as if no zones were defined."
			)
			# Reset to just OUTSIDE_ZONES_KEY if there was an error fetching actual zones
			time_spent_in_zones = {OUTSIDE_ZONES_KEY: 0}

	if not time_data or not heartrate_data:
		logger.warning("Time or HR data is missing. Cannot calculate time in zones.")
		return time_spent_in_zones

	if len(time_data) != len(heartrate_data):
		logger.warning(
			"Time and HR data lists have different lengths. "
			"Cannot accurately calculate time in zones."
		)
		return time_spent_in_zones

	if len(time_data) < 2:
		logger.info("Insufficient data points (need at least 2) to calculate time in zones.")
		return time_spent_in_zones

	moving_threshold = MOVING_DISTANCE_THRESHOLDS[
		zones_config.activity_type if zones_config is not None else ActivityType.DEFAULT
	]
	for idx in range(1, len(heartrate_data)):
		# Skip non-moving times if data available
		if not _is_moving_datapoint(moving_data, distance_data, moving_threshold, idx):
			continue

		if (duration := time_data[idx] - time_data[idx - 1]) <= 0:
			continue

		# Take the average of the current and previous heart rate data points
		heart_rate = round((heartrate_data[idx] + heartrate_data[idx - 1]) / 2)
		if zone_name := determine_hr_zone(heart_rate, zones_config):
			time_spent_in_zones[zone_name] = time_spent_in_zones.get(zone_name, 0) + duration
		else:
			time_spent_in_zones[OUTSIDE_ZONES_KEY] += duration

	return time_spent_in_zones


def _is_moving_datapoint(
	moving_data: Sequence[bool] | None,
	distance_data: Sequence[float] | None,
	moving_threshold: float,
	idx: int,
) -> bool:
	# Cannot evaluate if moving/distance data are not available
	if not (moving_data and distance_data):
		return True
	return moving_data[idx] or (distance_data[idx] - distance_data[idx - 1] > moving_threshold)
