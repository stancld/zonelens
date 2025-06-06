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
		response.raise_for_status()
		return response.json()

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
		response.raise_for_status()
		return response.json()

	def delete_subscription(
		self, client_id: str | int, client_secret: str, subscription_id: str
	) -> None:
		response = requests.delete(
			STRAVA_PUSH_SUBSCRIPTIONS_URL,
			params={
				"client_id": client_id,
				"client_secret": client_secret,
				"subscription_id": subscription_id,
			},
		)
		response.raise_for_status()
