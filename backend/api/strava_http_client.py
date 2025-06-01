from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from api.logging import get_logger

if TYPE_CHECKING:
	from typing import Any

STRAVA_PUSH_SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


class StravaHttpClient:
	"""HTTP Client for managing subscriptions to Strava push events."""

	def __init__(self) -> None:
		self.logger = get_logger(__name__)

	def get_subscriptions(self, client_id: str | int, client_secret: str) -> dict[str, Any]:
		response = requests.get(
			STRAVA_PUSH_SUBSCRIPTIONS_URL,
			params={"client_id": client_id, "client_secret": client_secret},
		)
		self.logger.info(f"Status Code: {response.status_code}")
		try:
			return response.json()
		except Exception as e:
			self.logger.error(f"Failed to get subscription: {e}")
			raise

	def register_subscription(
		self, client_id: str | int, client_secret: str, callback_url: str, verify_token: str
	) -> dict[str, Any]:
		response = requests.post(
			STRAVA_PUSH_SUBSCRIPTIONS_URL,
			params={
				"client_id": client_id,
				"client_secret": client_secret,
				"callback_url": callback_url,
				"verify_token": verify_token,
			},
		)
		self.logger.info(f"Status Code: {response.status_code}")
		try:
			return response.json()
		except Exception as e:
			self.logger.error(f"Failed to register subscription: {e}")
			raise

	def delete_subscription(
		self, client_id: str | int, client_secret: str, subscription_id: str
	) -> dict[str, Any]:
		response = requests.delete(
			STRAVA_PUSH_SUBSCRIPTIONS_URL,
			params={
				"client_id": client_id,
				"client_secret": client_secret,
				"subscription_id": subscription_id,
			},
		)
		self.logger.info(f"Status Code: {response.status_code}")
		try:
			return response.json()
		except Exception as e:
			self.logger.error(f"Failed to delete subscription: {e}")
			raise
