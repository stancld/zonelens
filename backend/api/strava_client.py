from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import pytz
import requests
from django.conf import settings

from api.logging import get_logger
from api.utils import decrypt_data

if TYPE_CHECKING:
	from typing import Any, TypedDict

	from api.models import StravaUser
	from api.types import Secret

	class RefreshTokenPayload(TypedDict):
		client_id: str
		client_secret: Secret
		refresh_token: str
		grant_type: str


logger = get_logger(__name__)

# Strava API Endpoints
STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL = f"{STRAVA_API_BASE_URL}/oauth/token"
STRAVA_API_ACTIVITIES_URL = f"{STRAVA_API_BASE_URL}/athlete/activities"
STRAVA_API_STREAMS_URL_TEMPLATE = f"{STRAVA_API_BASE_URL}/activities/{{activity_id}}/streams"
STRAVA_API_ATHLETE_ZONES_URL = f"{STRAVA_API_BASE_URL}/athlete/zones"
STRAVA_API_ACTIVITY_DETAIL_URL_TEMPLATE = f"{STRAVA_API_BASE_URL}/activities/{{activity_id}}"

# Default per_page, Strava API max is 200
STRAVA_API_MAX_PER_PAGE = 200


class StravaApiClient:
	def __init__(self, strava_user: StravaUser) -> None:
		self._strava_user = strava_user

	@property
	def strava_user(self) -> StravaUser:
		return self._strava_user

	@property
	def access_token(self) -> str:
		if self.strava_user.access_token is None:
			self.refresh_strava_token()
		if self.strava_user.access_token is None:
			raise ValueError("Access token is not available.")
		return decrypt_data(self.strava_user.access_token)

	@staticmethod
	def get(
		url: str, access_token: str, params: dict[str, Any] | None = None
	) -> requests.Response:
		return requests.get(
			url, headers={"Authorization": f"Bearer {access_token}"}, params=params
		)

	def refresh_strava_token(self) -> bool:
		"""Refreshes an expired Strava access token for the given user.

		Returns
		-------
			True if the token was successfully refreshed and saved, False otherwise.
		"""
		if not self.strava_user.refresh_token:
			logger.error(f"User {self.strava_user.strava_id} has no refresh token for Strava.")
			return False

		try:
			logger.info(
				f"Attempting to refresh Strava token for user {self.strava_user.strava_id}"
			)
			response = requests.post(
				STRAVA_TOKEN_URL,
				data=self._generate_refresh_token_payload(self.strava_user.refresh_token),
			)
			response.raise_for_status()
			token_data = response.json()

			self._update_strava_user_tokens(self.strava_user, token_data)
			logger.info(
				f"Successfully refreshed Strava token for user {self.strava_user.strava_id}"
			)
			return True
		except requests.exceptions.HTTPError as e:
			status_code = e.response.status_code if e.response is not None else "Unknown"
			text = e.response.text if e.response is not None else "No response body"
			logger.error(
				f"HTTP error refreshing Strava token for user {self.strava_user.strava_id}: "
				f"{status_code} {text}"
			)
			return False
		except (requests.exceptions.RequestException, ValueError, KeyError) as e:
			logger.error(
				f"Error refreshing Strava token for user {self.strava_user.strava_id}: {e}"
			)
			return False

	def fetch_strava_activities(
		self,
		page: int = 1,
		per_page: int = 200,
		before: int | None = None,
		after: int | None = None,
	) -> list[dict[str, Any]] | None:
		"""Fetches a list of activities for the authenticated Strava user.

		Additional parameters
		---------------------
			before: Epoch timestamp to filter activities before this time.
			after: Epoch timestamp to filter activities after this time.

		Returns
		-------
			A list of activity data as dictionaries, or None if an error occurs.
		"""
		if not self.strava_user.access_token:
			# If no access token, attempt refresh if refresh_token exists.
			# This handles cases where token might be missing (e.g. app restart).
			logger.info(f"No access token for user {self.strava_user.strava_id}, trying refresh.")
			if not self.refresh_strava_token():
				raise ValueError(
					f"Cannot retrieve access token for user {self.strava_user.strava_id}."
				)

		params: dict[str, int | str] = {"page": page, "per_page": per_page}
		if before is not None:
			params["before"] = before
		if after is not None:
			params["after"] = after

		try:
			response = self.get(
				STRAVA_API_ACTIVITIES_URL,
				access_token=self.access_token,
				params=params,
			)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.HTTPError as e:
			status_code = e.response.status_code if e.response is not None else None
			text = e.response.text if e.response is not None else "No response body"

			if status_code == 401:
				logger.info(f"401 for user {self.strava_user.strava_id}. Refreshing token.")
				if self.refresh_strava_token():
					logger.info(f"Token refreshed for {self.strava_user.strava_id}. Retrying.")
					try:
						response = self.get(
							STRAVA_API_ACTIVITIES_URL,
							access_token=self.access_token,
							params=params,
						)
						response.raise_for_status()
						return response.json()
					except requests.exceptions.HTTPError as retry_e:
						retry_status = retry_e.response.status_code if retry_e.response else "N/A"
						retry_text = retry_e.response.text if retry_e.response else ""
						logger.error(
							f"HTTP error on retry for {self.strava_user.strava_id} post-refresh: "
							f"{retry_status} {retry_text[:50]}"  # Truncate text
						)
					except (requests.exceptions.RequestException, ValueError) as retry_general_e:
						logger.error(
							f"Error on retry for {self.strava_user.strava_id} post-refresh: "
							f"{retry_general_e}"
						)
				else:
					logger.error(f"Refresh failed for {self.strava_user.strava_id}. Cannot retry.")
			else:
				logger.error(
					f"HTTP error for {self.strava_user.strava_id} (not 401): "
					f"{status_code or 'N/A'} {text[:70]}"  # Truncate text
				)
		except requests.exceptions.RequestException as e:
			logger.error(f"Request error for {self.strava_user.strava_id}: {e}")
		except ValueError as e:
			logger.error(f"JSON decode error for {self.strava_user.strava_id}: {e}")
		return None

	def fetch_athlete_zones(self) -> dict[str, dict[str, Any]] | None:
		"""Fetches the athlete's defined zones (HR, Power) from Strava.

		Returns
		-------
			A dict of zone data as dictionaries, or None if an error occurs.
		"""
		logger.info(f"Fetching athlete zones for user {self.strava_user.strava_id}")
		if not self.strava_user.access_token:
			logger.info(f"No access token for user {self.strava_user.strava_id}, trying refresh.")
			if not self.refresh_strava_token():
				raise ValueError(
					f"Cannot retrieve access token for user {self.strava_user.strava_id}."
				)

		try:
			response = self.get(STRAVA_API_ATHLETE_ZONES_URL, access_token=self.access_token)
			response.raise_for_status()
			return response.json()
		except requests.exceptions.HTTPError as e:
			status_code = e.response.status_code if e.response is not None else None
			text = e.response.text if e.response is not None else "No response body"

			if status_code == 401:
				logger.info(
					f"401 fetching zones for user {self.strava_user.strava_id}. Refreshing token."
				)
				if self.refresh_strava_token():
					logger.info(
						f"Token refreshed for {self.strava_user.strava_id}. Retrying zones fetch."
					)
					try:
						response = self.get(
							STRAVA_API_ATHLETE_ZONES_URL, access_token=self.access_token
						)
						response.raise_for_status()
						return response.json()
					except requests.exceptions.HTTPError as retry_e:
						retry_status = retry_e.response.status_code if retry_e.response else "N/A"
						retry_text = retry_e.response.text if retry_e.response else ""
						logger.error(
							f"Retry HTTP error fetching zones for {self.strava_user.strava_id}: "
							f"{retry_status} {retry_text[:50]}"
						)
					except (requests.exceptions.RequestException, ValueError) as retry_general_e:
						logger.error(
							f"Retry error for {self.strava_user.strava_id} (zones): "
							f"{retry_general_e}"
						)
				else:
					logger.error(
						f"Refresh failed for {self.strava_user.strava_id}, cannot retry zones."
					)
			else:
				logger.error(
					f"HTTP error fetching zones for {self.strava_user.strava_id}: "
					f"{status_code or 'N/A'} {text[:50]}"
				)
		except requests.exceptions.RequestException as e:
			logger.error(f"Request error fetching zones for {self.strava_user.strava_id}: {e}")
		except ValueError as e:  # Includes JSONDecodeError
			logger.error(f"JSON decode error fetching zones for {self.strava_user.strava_id}: {e}")
		return None

	def fetch_all_strava_activities(
		self,
		before: int | None = None,
		after: int | None = None,
	) -> list[dict[str, Any]] | None:
		"""Fetches all activities for the authenticated Strava user by handling pagination.

		Additional parameters
		---------------------
			before: Epoch timestamp to filter activities before this time.
			after: Epoch timestamp to filter activities after this time.

		Returns
		-------
			A list of all activity data as dictionaries, or None if a persistent error occurs.
			If some activities are fetched before an error, those will be returned.
		"""
		all_activities: list[dict[str, Any]] = []
		page = 1

		logger.info(
			f"Starting to fetch all Strava activities for user {self.strava_user.strava_id}."
		)

		while True:
			logger.debug(
				f"Fetching page {page} of activities for user {self.strava_user.strava_id} "
				f"(before={before}, after={after})"
			)
			activities_chunk = self.fetch_strava_activities(
				page=page,
				per_page=STRAVA_API_MAX_PER_PAGE,
				before=before,
				after=after,
			)

			if activities_chunk is None:
				# An error occurred in fetch_strava_activities (already logged there)
				# This includes token refresh failures or persistent API errors.
				logger.error(
					f"Failed to fetch page {page} for user {self.strava_user.strava_id}. "
					f"Returning {len(all_activities)} activities fetched so far."
				)
				return all_activities if all_activities else None

			if not activities_chunk:
				logger.info(
					f"No more activities found on page {page} for user "
					f"{self.strava_user.strava_id}. "
					f"Total activities fetched: {len(all_activities)}."
				)
				break

			all_activities.extend(activities_chunk)
			logger.debug(
				f"Page {page}: {len(activities_chunk)} acts. Total: {len(all_activities)}."
			)

			page += 1

		return all_activities

	def fetch_activity_details(self, activity_id: int) -> dict[str, Any] | None:
		"""Fetches details for a single activity from the Strava API.

		Returns
		-------
		    A dictionary containing the activity data, or None if an error occurs.
		"""
		if not self.strava_user.access_token:
			logger.info(f"No access token for user {self.strava_user.strava_id}, trying refresh.")
			if not self.refresh_strava_token():
				raise ValueError(
					f"Cannot retrieve access token for user {self.strava_user.strava_id}."
				)

		try:
			response = StravaApiClient.get(
				url=STRAVA_API_ACTIVITY_DETAIL_URL_TEMPLATE.format(activity_id=activity_id),
				access_token=self.access_token,
			)
			response.raise_for_status()  # Raise HTTPError for bad responses (4XX or 5XX)
			return response.json()
		except requests.exceptions.HTTPError as e:
			logger.error(
				f"Request error fetching details for activity {activity_id} "
				f"for user {self.strava_user.strava_id}: {e}"
			)
			return None

	def fetch_activity_streams(
		self, activity_id: int, attempt_refresh: bool = True
	) -> dict[str, Any] | None:
		"""
		Fetches specified streams (heartrate, time) for a given activity from the Strava API.

		Additional parameters
		---------------------
			activity_id
				The ID of the activity for which to fetch streams.
			attempt_refresh
				If True, try to refresh the token on a 401 error.

		Returns
		-------
			A dictionary containing the stream data (e.g., {'time': {...}, 'heartrate': {...}})
			or None if an error occurs or streams are not available.
		"""
		if not self.access_token:
			logger.error(
				f"No access token available for Strava user {self.strava_user.strava_id}."
			)
			return None

		params = {"keys": "heartrate,time", "key_by_type": "true"}

		try:
			response = self.get(
				url=STRAVA_API_STREAMS_URL_TEMPLATE.format(activity_id=activity_id),
				access_token=self.access_token,
				params=params,
			)
			response.raise_for_status()  # Raise HTTPError for bad responses (4XX or 5XX)
			streams_data = response.json()
			logger.debug(
				f"Successfully fetched streams for activity {activity_id}. "
				f"Streams received: {list(streams_data.keys())}"
			)
			return streams_data
		except requests.exceptions.HTTPError as e:
			if e.response is not None and e.response.status_code == 401 and attempt_refresh:
				logger.warning(
					f"Token expired/invalid for user {self.strava_user.strava_id} "
					f"while fetching streams for activity {activity_id}. Attempting refresh."
				)
				if self.refresh_strava_token():
					logger.info(
						f"Token refreshed. Retrying stream fetch for activity {activity_id}."
					)
					return self.fetch_activity_streams(activity_id, attempt_refresh=False)
				err_msg = (
					f"Token refresh failed for user {self.strava_user.strava_id}. "
					f"Cannot fetch streams for activity {activity_id}."
				)
				raise ValueError(err_msg) from e
			if e.response is not None and e.response.status_code == 404:
				logger.warning(
					f"Act {activity_id} not found/no streams (user {self.strava_user.strava_id})."
					f" Details: {e.response.text}"
				)
			logger.error(
				f"HTTP err for act {activity_id} (user {self.strava_user.strava_id}): {e}. "
				f"Response: {e.response.text if e.response else 'N/A'}"
			)
		except requests.exceptions.RequestException as e:
			logger.error(
				f"Request error fetching streams for activity {activity_id} "
				f"for user {self.strava_user.strava_id}: {e}"
			)
		except ValueError as e:  # Includes JSONDecodeError
			logger.error(
				f"Error decoding JSON response for activity streams {activity_id} "
				f"for user {self.strava_user.strava_id}: {e}"
			)
		return None

	@staticmethod
	def _generate_refresh_token_payload(refresh_token: str) -> RefreshTokenPayload:
		return {
			"client_id": settings.STRAVA_CLIENT_ID,
			"client_secret": settings.STRAVA_CLIENT_SECRET,
			"refresh_token": refresh_token,
			"grant_type": "refresh_token",
		}

	@staticmethod
	def _update_strava_user_tokens(strava_user: StravaUser, token_data: dict[str, Any]) -> None:
		strava_user.access_token = token_data["access_token"]
		strava_user.refresh_token = token_data["refresh_token"]

		expires_at_timestamp = token_data["expires_at"]
		strava_user.token_expires_at = dt.datetime.fromtimestamp(expires_at_timestamp, tz=pytz.UTC)

		strava_user.save(update_fields=["_access_token", "_refresh_token", "token_expires_at"])
