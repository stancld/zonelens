from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import pytz
import requests
from django.conf import settings

if TYPE_CHECKING:
	from typing import Any, TypedDict

	from api.models import StravaUser
	from api.types import Secret

	class RefreshTokenPayload(TypedDict):
		client_id: str
		client_secret: Secret
		refresh_token: str
		grant_type: str


logger = logging.getLogger(__name__)

STRAVA_API_BASE_URL = "https://www.strava.com/api/v3"
STRAVA_API_ACTIVITIES_URL = f"{STRAVA_API_BASE_URL}/athlete/activities"
STRAVA_TOKEN_URL = f"{STRAVA_API_BASE_URL}/oauth/token"
STRAVA_API_MAX_PER_PAGE = 200


def refresh_strava_token(strava_user: StravaUser) -> bool:
	"""Refreshes an expired Strava access token for the given user.

	Returns
	-------
	    True if the token was successfully refreshed and saved, False otherwise.
	"""
	if not strava_user.refresh_token:
		logger.error(f"User {strava_user.strava_id} has no refresh token for Strava.")
		return False

	try:
		logger.info(f"Attempting to refresh Strava token for user {strava_user.strava_id}")
		response = requests.post(
			STRAVA_TOKEN_URL, data=_generate_refresh_token_payload(strava_user.refresh_token)
		)
		response.raise_for_status()
		token_data = response.json()

		_update_strava_user_tokens(strava_user, token_data)
		logger.info(f"Successfully refreshed Strava token for user {strava_user.strava_id}")
		return True
	except requests.exceptions.HTTPError as e:
		status_code = e.response.status_code if e.response is not None else "Unknown"
		text = e.response.text if e.response is not None else "No response body"
		logger.error(
			f"HTTP error refreshing Strava token for user {strava_user.strava_id}: "
			f"{status_code} {text}"
		)
		return False
	except (requests.exceptions.RequestException, ValueError, KeyError) as e:
		logger.error(f"Error refreshing Strava token for user {strava_user.strava_id}: {e}")
		return False


def fetch_strava_activities(
	strava_user: StravaUser,
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
	if not strava_user.access_token:
		# If no access token, attempt refresh if refresh_token exists.
		# This handles cases where token might be missing (e.g. app restart).
		logger.info(f"No access token for user {strava_user.strava_id}, trying refresh.")
		if not refresh_strava_token(strava_user):
			logger.error(f"User {strava_user.strava_id} has no access token & refresh failed.")
			return None

	headers = {"Authorization": f"Bearer {strava_user.access_token}"}
	params: dict[str, int | str] = {"page": page, "per_page": per_page}

	if before is not None:
		params["before"] = before
	if after is not None:
		params["after"] = after

	try:
		response = requests.get(STRAVA_API_ACTIVITIES_URL, headers=headers, params=params)
		response.raise_for_status()
		return response.json()
	except requests.exceptions.HTTPError as e:
		status_code = e.response.status_code if e.response is not None else None
		text = e.response.text if e.response is not None else "No response body"

		if status_code == 401:
			logger.info(f"401 for user {strava_user.strava_id}. Refreshing token.")
			if refresh_strava_token(strava_user):
				logger.info(f"Token refreshed for {strava_user.strava_id}. Retrying.")
				headers["Authorization"] = f"Bearer {strava_user.access_token}"
				try:
					response = requests.get(
						STRAVA_API_ACTIVITIES_URL, headers=headers, params=params
					)
					response.raise_for_status()
					return response.json()
				except requests.exceptions.HTTPError as retry_e:
					retry_status = retry_e.response.status_code if retry_e.response else "N/A"
					retry_text = retry_e.response.text if retry_e.response else ""
					logger.error(
						f"HTTP error on retry for {strava_user.strava_id} post-refresh: "
						f"{retry_status} {retry_text[:50]}"  # Truncate text
					)
				except (requests.exceptions.RequestException, ValueError) as retry_general_e:
					logger.error(
						f"Error on retry for {strava_user.strava_id} post-refresh: "
						f"{retry_general_e}"
					)
			else:
				logger.error(f"Refresh failed for {strava_user.strava_id}. Cannot retry.")
		else:
			logger.error(
				f"HTTP error for {strava_user.strava_id} (not 401): "
				f"{status_code or 'N/A'} {text[:70]}"  # Truncate text
			)
	except requests.exceptions.RequestException as e:
		logger.error(f"Request error for {strava_user.strava_id}: {e}")
	except ValueError as e:
		logger.error(f"JSON decode error for {strava_user.strava_id}: {e}")
	return None


def fetch_all_strava_activities(
	strava_user: StravaUser,
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

	logger.info(f"Starting to fetch all Strava activities for user {strava_user.strava_id}.")

	while True:
		logger.debug(
			f"Fetching page {page} of activities for user {strava_user.strava_id} "
			f"(before={before}, after={after})"
		)
		activities_chunk = fetch_strava_activities(
			strava_user=strava_user,
			page=page,
			per_page=STRAVA_API_MAX_PER_PAGE,
			before=before,
			after=after,
		)

		if activities_chunk is None:
			# An error occurred in fetch_strava_activities (already logged there)
			# This includes token refresh failures or persistent API errors.
			logger.error(
				f"Failed to fetch page {page} for user {strava_user.strava_id}. "
				f"Returning {len(all_activities)} activities fetched so far."
			)
			return all_activities if all_activities else None

		if not activities_chunk:
			logger.info(
				f"No more activities found on page {page} for user {strava_user.strava_id}. "
				f"Total activities fetched: {len(all_activities)}."
			)
			break

		all_activities.extend(activities_chunk)
		logger.debug(f"Page {page}: {len(activities_chunk)} acts. Total: {len(all_activities)}.")

		page += 1

	return all_activities


def _generate_refresh_token_payload(refresh_token: str) -> RefreshTokenPayload:
	return {
		"client_id": settings.STRAVA_CLIENT_ID,
		"client_secret": settings.STRAVA_CLIENT_SECRET,
		"refresh_token": refresh_token,
		"grant_type": "refresh_token",
	}


def _update_strava_user_tokens(strava_user: StravaUser, token_data: dict[str, Any]) -> None:
	strava_user.access_token = token_data["access_token"]
	strava_user.refresh_token = token_data["refresh_token"]

	expires_at_timestamp = token_data["expires_at"]
	strava_user.token_expires_at = dt.datetime.fromtimestamp(expires_at_timestamp, tz=pytz.UTC)

	strava_user.save(update_fields=["_access_token", "_refresh_token", "token_expires_at"])
