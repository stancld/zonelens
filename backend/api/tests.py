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

import json
import unittest.mock
from datetime import datetime, timedelta
from importlib import import_module
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytz
import requests_mock
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages, storage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.hr_processing import (
	OUTSIDE_ZONES_KEY,
	_is_moving_datapoint,
	calculate_time_in_zones,
	determine_hr_zone,
	parse_activity_streams,
)
from api.models import (
	ActivityType,
	ActivityZoneTimes,
	CustomZonesConfig,
	HeartRateZone,
	StravaUser,
	ZoneSummary,
)
from api.strava_client import (
	STRAVA_API_ACTIVITIES_URL,
	STRAVA_API_MAX_PER_PAGE,
	STRAVA_API_STREAMS_URL_TEMPLATE,
	STRAVA_TOKEN_URL,
	StravaApiClient,
)
from api.utils import decrypt_data, encrypt_data
from api.views import UserHRZonesDisplayView
from api.worker import Worker

if TYPE_CHECKING:
	from django.http import HttpRequest, HttpResponse

User = get_user_model()


class InitialMigrationTests(TestCase):
	@property
	def user(self) -> StravaUser:
		return StravaUser.objects.create(
			strava_id=12345,
			_access_token="dummy_encrypted_token",
			_refresh_token="dummy_encrypted_refresh",
			token_expires_at=datetime.now(),
			scope="read,activity:read_all",
		)

	def test_strava_user_can_be_created(self) -> None:
		user = StravaUser.objects.create(
			strava_id=12345,
			_access_token="dummy_encrypted_token",
			_refresh_token="dummy_encrypted_refresh",
			token_expires_at=datetime.now(),
			scope="read,activity:read_all",
		)
		self.assertIsNotNone(user.pk)  # Check if saved
		self.assertEqual(user.strava_id, 12345)

	def test_zone_config_can_be_created(self) -> None:
		zone_config = CustomZonesConfig.objects.create(
			user=self.user, activity_type=ActivityType.RUN
		)
		self.assertIsNotNone(zone_config.pk)
		self.assertEqual(zone_config.activity_type, ActivityType.RUN)

	def test_summary_can_be_created(self) -> None:
		zone_summary = ZoneSummary.objects.create(
			user=self.user,
			period_type=ZoneSummary.PeriodType.WEEKLY,
			year=2025,
			period_index=18,
			zone_times_seconds=json.dumps({"Zone 1": 3600, "Zone 2": 1800}),
		)
		self.assertIsNotNone(zone_summary.pk)
		self.assertEqual(zone_summary.year, 2025)
		self.assertEqual(zone_summary.period_type, ZoneSummary.PeriodType.WEEKLY)
		self.assertEqual(zone_summary.period_index, 18)


# Sample Strava API responses
MOCK_STRAVA_TOKEN_RESPONSE = {
	"token_type": "Bearer",
	"expires_at": 2175763419,  # Some future date
	"expires_in": 21600,
	"refresh_token": "test_refresh_token_123",
	"access_token": "test_access_token_123",
	"athlete": {
		"id": 12345,
		"username": "test_strava_user",
		"resource_state": 2,
		"firstname": "Test",
		"lastname": "User",
		"city": "Test City",
		"state": "TS",
		"country": "Testland",
		"sex": "M",
		"premium": False,
		"summit": False,
		"created_at": "2024-01-01T10:00:00Z",
		"updated_at": "2024-01-01T10:00:00Z",
		"badge_type_id": 0,
		"profile_medium": "https://example.com/medium.jpg",
		"profile": "https://example.com/large.jpg",
	},
}
MOCK_STRAVA_ERROR_RESPONSE = {
	"message": "Bad Request",
	"errors": [{"resource": "Application", "field": "client_id", "code": "invalid"}],
}


class AuthViewTests(APITestCase):
	def test_strava_authorize_redirect(self):
		"""Test that the strava_authorize view redirects correctly."""
		url = reverse("strava_authorize")
		response = self.client.get(url)
		self.assertEqual(response.status_code, status.HTTP_302_FOUND)
		self.assertTrue(response.url.startswith("https://www.strava.com/oauth/authorize"))
		self.assertIn("client_id=", response.url)
		self.assertIn("redirect_uri=", response.url)
		self.assertIn("response_type=code", response.url)
		# Check combined scope
		self.assertIn("scope=read%2Cactivity%3Aread_all%2Cprofile%3Aread_all", response.url)

	def test_user_profile_authenticated(self):
		"""Test retrieving user profile with valid token authentication."""
		# Create a user and associated token first
		user = get_user_model().objects.create_user(username="testuser", password="password")
		_strava_user = StravaUser.objects.create(
			user=user,
			strava_id=98765,
			_access_token=encrypt_data("dummy_access"),
			_refresh_token=encrypt_data("dummy_refresh"),
			token_expires_at=timezone.now() + timezone.timedelta(hours=1),
			scope="read,activity:read_all",
		)
		token = Token.objects.create(user=user)

		url = reverse("user_profile")
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
		response = self.client.get(url)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data["username"], "testuser")
		self.assertEqual(response.data["strava_id"], 98765)

	def test_user_profile_unauthenticated(self):
		"""Test retrieving user profile without authentication."""
		url = reverse("user_profile")
		response = self.client.get(url)
		# TODO: Should return 401, but with CORS middleware we get 403 now
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	@requests_mock.Mocker()
	def test_strava_callback_success(self, mocker: requests_mock.Mocker) -> None:
		"""Test successful Strava callback and token generation."""
		# Mock the Strava token exchange endpoint
		mocker.post(
			"https://www.strava.com/oauth/token",
			json=MOCK_STRAVA_TOKEN_RESPONSE,
			status_code=status.HTTP_200_OK,
		)

		callback_url = reverse("strava_callback")
		# Simulate Strava redirecting back with a code and scope
		response = self.client.get(
			callback_url, {"code": "test_auth_code", "scope": "activity:read_all,profile:read_all"}
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertTemplateUsed(response, "api/auth_callback.html")
		self.assertIn("token", response.context)
		self.assertIn("frontend_redirect_url", response.context)

		# Verify Django User and StravaUser were created
		strava_id = MOCK_STRAVA_TOKEN_RESPONSE["athlete"]["id"]  # type: ignore[index]
		django_user = get_user_model().objects.get(username=f"strava_{strava_id}")
		self.assertEqual(
			django_user.first_name,
			MOCK_STRAVA_TOKEN_RESPONSE["athlete"]["firstname"],  # type: ignore[index]
		)
		strava_user = StravaUser.objects.get(strava_id=strava_id)
		self.assertEqual(strava_user.user, django_user)
		self.assertEqual(strava_user.scope, "activity:read_all,profile:read_all")

		# Verify token exists and belongs to the user
		token = Token.objects.get(user=django_user)
		self.assertEqual(response.context["token"], token.key)

		# Verify user is logged in (session established by login() call)
		self.assertTrue(
			response.wsgi_request.user.is_authenticated,
			"User should be authenticated in the session after login() call.",
		)
		self.assertEqual(
			response.wsgi_request.user,
			django_user,
			"The authenticated session user should be the one created/retrieved during callback.",
		)

		# Verify tokens are stored encrypted (cannot check exact value easily)
		self.assertNotEqual(strava_user._access_token, "test_access_token_123")
		self.assertTrue(len(strava_user._access_token) > 50)  # Encrypted tokens are longer

	def test_strava_callback_strava_error(self) -> None:
		"""Test Strava callback when Strava returns an error parameter."""
		callback_url = reverse("strava_callback")
		response = self.client.get(callback_url, {"error": "access_denied"})
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn(b"Error received from Strava: access_denied", response.content)

	@requests_mock.Mocker()
	def test_strava_callback_token_exchange_error(self, mocker: requests_mock.Mocker) -> None:
		"""Test Strava callback when token exchange fails."""
		# Mock the Strava token exchange endpoint to return an error
		mocker.post(
			"https://www.strava.com/oauth/token",
			json=MOCK_STRAVA_ERROR_RESPONSE,
			status_code=status.HTTP_400_BAD_REQUEST,
		)

		callback_url = reverse("strava_callback")
		response = self.client.get(
			callback_url, {"code": "invalid_auth_code", "scope": "activity:read_all"}
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn(b"Failed to authenticate with Strava.", response.content)


class UtilTests(TestCase):
	def test_encryption_decryption(self) -> None:
		"""Test that encrypt_data and decrypt_data work correctly."""
		original_data = "my_secret_access_token"
		encrypted = encrypt_data(original_data)
		decrypted = decrypt_data(encrypted)

		self.assertNotEqual(original_data, encrypted)
		self.assertTrue(isinstance(encrypted, str))  # Ensure it returns string
		self.assertEqual(original_data, decrypted)

	def test_decrypt_empty_string(self) -> None:
		"""Test decrypting an empty string returns an empty string."""
		self.assertEqual(decrypt_data(""), "")

	def test_encrypt_empty_string(self) -> None:
		"""Test encrypting an empty string returns an empty string."""
		self.assertEqual(encrypt_data(""), "")


class StravaUserModelTests(TestCase):
	def test_token_properties_encryption(self) -> None:
		"""Test the access_token and refresh_token properties handle encryption."""
		strava_user = StravaUser(strava_id=1111)
		access_token_plain = "access_123"
		refresh_token_plain = "refresh_456"

		strava_user.access_token = access_token_plain
		strava_user.refresh_token = refresh_token_plain

		# Check internal storage is encrypted
		self.assertNotEqual(strava_user._access_token, access_token_plain)
		self.assertTrue(len(strava_user._access_token) > 0)
		self.assertNotEqual(strava_user._refresh_token, refresh_token_plain)
		self.assertTrue(len(strava_user._refresh_token) > 0)

		# Check properties return decrypted values
		self.assertEqual(strava_user.access_token, access_token_plain)
		self.assertEqual(strava_user.refresh_token, refresh_token_plain)

	def test_token_properties_empty(self) -> None:
		"""Test token properties handle empty values correctly."""
		strava_user = StravaUser(strava_id=2222)

		strava_user.access_token = ""
		strava_user.refresh_token = ""

		self.assertEqual(strava_user.access_token, "")
		self.assertEqual(strava_user.refresh_token, "")

		# Test accessing properties when internal fields are default
		strava_user_default = StravaUser(strava_id=3333)
		self.assertEqual(strava_user_default.access_token, "")
		self.assertEqual(strava_user_default.refresh_token, "")


class CustomZonesSettingsViewTests(APITestCase):
	def setUp(self) -> None:
		"""Set up a user, StravaUser, and token for authentication."""
		self.django_user = get_user_model().objects.create_user(
			username="testuser_zones", password="password123"
		)
		self.strava_user = StravaUser.objects.create(
			user=self.django_user,
			strava_id=112233,
			_access_token=encrypt_data("dummy_access"),
			_refresh_token=encrypt_data("dummy_refresh"),
			token_expires_at=timezone.now() + timezone.timedelta(hours=1),
			scope="read,activity:read_all",
		)
		self.token = Token.objects.create(user=self.django_user)
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
		self.url = reverse("custom_zones_settings")
		self.sample_payload = {
			"activity_type": "RUN",
			"zones_definition": [
				{"name": "Z1", "min_hr": 100, "max_hr": 120, "order": 1},
				{"name": "Z2", "min_hr": 121, "max_hr": 140, "order": 2},
			],
		}

	def test_get_zone_settings_unauthenticated(self) -> None:
		self.client.credentials()  # Clear credentials
		response = self.client.get(self.url)
		# TODO: Should return 401, but with CORS middleware we get 403 now
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_get_zone_settings_authenticated_empty(self) -> None:
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data, [])

	def test_post_zone_settings_unauthenticated(self) -> None:
		self.client.credentials()  # Clear credentials
		response = self.client.post(self.url, self.sample_payload, format="json")
		# TODO: Should return 401, but with CORS middleware we get 403 now
		self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

	def test_post_zone_settings_success(self) -> None:
		response = self.client.post(self.url, self.sample_payload, format="json")
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		self.assertEqual(response.data["activity_type"], "RUN")
		self.assertEqual(len(response.data["zones_definition"]), 2)
		self.assertEqual(response.data["zones_definition"][0]["name"], "Z1")
		self.assertTrue(CustomZonesConfig.objects.filter(user=self.strava_user).exists())
		config = CustomZonesConfig.objects.get(user=self.strava_user)
		self.assertEqual(config.zones_definition.count(), 2)

	def test_post_zone_settings_invalid_payload_missing_activity_type(self) -> None:
		payload = self.sample_payload.copy()
		del payload["activity_type"]
		response = self.client.post(self.url, payload, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("activity_type", response.data)

	def test_post_zone_settings_invalid_payload_malformed_zones(self) -> None:
		payload = {
			"activity_type": "RIDE",
			"zones_definition": [{"name": "Z1"}],  # Missing min_hr, max_hr, order
		}
		response = self.client.post(self.url, payload, format="json")
		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertIn("zones_definition", response.data)

	def test_get_zone_settings_after_post(self) -> None:
		self.client.post(self.url, self.sample_payload, format="json")  # Create one
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response.data), 1)
		self.assertEqual(response.data[0]["activity_type"], "RUN")
		self.assertEqual(len(response.data[0]["zones_definition"]), 2)


@unittest.mock.patch("api.strava_client.decrypt_data", lambda secret: secret)
class StravaApiClientFunctionTests(TestCase):
	def setUp(self) -> None:
		"""Set up a user and StravaUser for testing client functions."""
		self.django_user = get_user_model().objects.create_user(
			username="test_strava_client_user", password="password"
		)
		self.strava_user = StravaUser.objects.create(
			user=self.django_user,
			strava_id=78910,
			token_expires_at=timezone.now() + timezone.timedelta(hours=1),
			scope="activity:read_all",
		)
		# Set tokens using the property setters to ensure encryption
		self.strava_user.access_token = "mock_valid_access_token"
		self.strava_user.refresh_token = "mock_valid_refresh_token"
		self.strava_user.save()
		self.strava_client = StravaApiClient(self.strava_user)

	@requests_mock.Mocker()
	def test_fetch_all_acts_multiple_pages_no_refresh(self, mocker: requests_mock.Mocker) -> None:
		"""Test fetching all activities across multiple pages without token refresh."""
		# Mock activities data
		mock_activities_page1 = [
			{"id": i, "name": f"Act {i}"} for i in range(1, STRAVA_API_MAX_PER_PAGE + 1)
		]
		mock_activities_page2 = [
			{"id": i, "name": f"Act {i}"}
			for i in range(STRAVA_API_MAX_PER_PAGE + 1, STRAVA_API_MAX_PER_PAGE + 11)
		]

		# Mock GET requests for activities
		mocker.get(
			STRAVA_API_ACTIVITIES_URL,
			[
				{"json": mock_activities_page1, "status_code": 200},
				{"json": mock_activities_page2, "status_code": 200},
				{"json": [], "status_code": 200},  # Empty list to terminate
			],
		)

		# Mock POST request for token refresh (we assert it's not called)
		mocker.post(STRAVA_TOKEN_URL, status_code=200, json=MOCK_STRAVA_TOKEN_RESPONSE)

		all_activities = self.strava_client.fetch_all_strava_activities()

		self.assertIsNotNone(all_activities)
		self.assertEqual(len(all_activities), STRAVA_API_MAX_PER_PAGE + 10)  # type: ignore[arg-type]
		self.assertEqual(all_activities[0]["id"], 1)  # type: ignore[index]
		self.assertEqual(all_activities[-1]["id"], STRAVA_API_MAX_PER_PAGE + 10)  # type: ignore[index]

		# Check calls to GET /activities
		activity_url_path = urlparse(STRAVA_API_ACTIVITIES_URL).path
		activity_calls = [
			call
			for call in mocker.request_history
			if call.path == activity_url_path and call.method == "GET"
		]
		self.assertEqual(len(activity_calls), 3)

		# Check page parameters in GET requests
		self.assertEqual(activity_calls[0].qs["page"], ["1"])
		self.assertEqual(activity_calls[0].qs["per_page"], [str(STRAVA_API_MAX_PER_PAGE)])
		self.assertEqual(activity_calls[1].qs["page"], ["2"])
		self.assertEqual(activity_calls[1].qs["per_page"], [str(STRAVA_API_MAX_PER_PAGE)])
		self.assertEqual(activity_calls[2].qs["page"], ["3"])
		self.assertEqual(activity_calls[2].qs["per_page"], [str(STRAVA_API_MAX_PER_PAGE)])

		# Check that token refresh was not called
		token_url_path = urlparse(STRAVA_TOKEN_URL).path
		refresh_calls = [
			call
			for call in mocker.request_history
			if call.path == token_url_path and call.method == "POST"
		]
		self.assertEqual(len(refresh_calls), 0)

	@override_settings(
		STRAVA_CLIENT_ID="test_client_id", STRAVA_CLIENT_SECRET="test_client_secret"
	)
	@requests_mock.Mocker()
	def test_fetch_all_acts_token_refresh_success(self, mocker: requests_mock.Mocker) -> None:
		"""Test fetching all activities with a successful token refresh mid-fetch."""

		# Simulate an expired token
		self.strava_user.token_expires_at = timezone.now() - timezone.timedelta(hours=1)
		self.strava_user.save()

		# Mock activities data
		mock_activities_page1 = [
			{"id": i, "name": f"Act {i}"} for i in range(1, STRAVA_API_MAX_PER_PAGE + 1)
		]
		mock_activities_page2 = [
			{"id": i, "name": f"Act {i}"}
			for i in range(STRAVA_API_MAX_PER_PAGE + 1, STRAVA_API_MAX_PER_PAGE + 11)
		]

		# Mock response for successful token refresh
		new_access_token = "new_mock_access_token_refreshed"
		new_refresh_token = "new_mock_refresh_token_refreshed"
		new_expires_at = int((timezone.now() + timezone.timedelta(hours=6)).timestamp())
		mock_refresh_response = {
			"access_token": new_access_token,
			"refresh_token": new_refresh_token,
			"expires_at": new_expires_at,
		}
		mocker.post(STRAVA_TOKEN_URL, json=mock_refresh_response, status_code=200)

		# Mock GET requests for activities:
		# 1. Initial call with old token -> 401
		# 2. Call with new token -> page 1
		# 3. Call with new token -> page 2
		# 4. Call with new token -> empty page
		mocker.get(
			STRAVA_API_ACTIVITIES_URL,
			[
				{"status_code": 401, "json": {"message": "Unauthorized"}},  # Initial 401
				{"json": mock_activities_page1, "status_code": 200},  # Retry for page 1
				{"json": mock_activities_page2, "status_code": 200},  # Page 2
				{"json": [], "status_code": 200},  # Empty list to terminate
			],
		)

		all_activities = self.strava_client.fetch_all_strava_activities()

		self.assertIsNotNone(all_activities)
		self.assertEqual(len(all_activities), STRAVA_API_MAX_PER_PAGE + 10)  # type: ignore[arg-type]

		# Check that token refresh was called once
		token_url_path = urlparse(STRAVA_TOKEN_URL).path
		refresh_calls = [
			call
			for call in mocker.request_history
			if call.path == token_url_path and call.method == "POST"
		]
		self.assertEqual(len(refresh_calls), 1)

		# Verify StravaUser tokens were updated
		self.strava_user.refresh_from_db()
		self.assertEqual(self.strava_user.access_token, new_access_token)
		self.assertEqual(self.strava_user.refresh_token, new_refresh_token)
		# Compare timestamps directly for expires_at
		self.assertEqual(self.strava_user.token_expires_at.timestamp(), new_expires_at)

		# Check calls to GET /activities
		activity_url_path = urlparse(STRAVA_API_ACTIVITIES_URL).path
		activity_calls = [
			call
			for call in mocker.request_history
			if call.path == activity_url_path and call.method == "GET"
		]
		self.assertEqual(len(activity_calls), 4)  # 1 fail (401), 3 success

		# Check Authorization header for successful calls used the new token
		# The first call in activity_calls list would be the 401, so we skip it.
		expected_auth_header = f"Bearer {new_access_token}"
		for i in range(1, len(activity_calls)):
			self.assertEqual(activity_calls[i].headers["Authorization"], expected_auth_header)

		# Check page parameters for successful calls
		# activity_calls[0] is the 401 error, so we check from activity_calls[1]
		self.assertEqual(activity_calls[1].qs["page"], ["1"])
		self.assertEqual(activity_calls[2].qs["page"], ["2"])
		self.assertEqual(activity_calls[3].qs["page"], ["3"])

	@patch("api.strava_client.StravaApiClient.get")
	def test_fetch_activity_streams_success(self, mock_strava_get: MagicMock) -> None:
		"""Test successfully fetching activity streams."""
		activity_id = 1234567890
		mock_stream_data = {
			"time": {"data": [0, 1, 2, 3], "original_size": 4},
			"heartrate": {"data": [120, 122, 125, 128], "original_size": 4},
		}

		# Configure the mock response object
		mock_response = MagicMock()
		mock_response.json.return_value = mock_stream_data
		mock_response.raise_for_status = MagicMock()  # Ensure it doesn't raise an error
		mock_strava_get.return_value = mock_response

		# Ensure the StravaUser has an access token for the test
		# The setUp method already provides self.strava_user with encrypted tokens.
		# We can assume the .access_token property decrypts it.
		self.strava_user._access_token = encrypt_data("test_valid_access_token")
		self.strava_user.save()

		streams = self.strava_client.fetch_activity_streams(activity_id)

		self.assertIsNotNone(streams)
		self.assertEqual(streams, mock_stream_data)

		expected_url = STRAVA_API_STREAMS_URL_TEMPLATE.format(activity_id=activity_id)
		expected_params = {"keys": "heartrate,time,distance,moving", "key_by_type": "true"}

		mock_strava_get.assert_called_once_with(
			url=expected_url,
			access_token="test_valid_access_token",  # This should be the decrypted token
			params=expected_params,
		)


class HRProcessingTests(APITestCase):
	def setUp(self) -> None:
		"""Set up common test data."""
		# First, create a standard Django User
		django_user = get_user_model().objects.create_user(
			username="hr_test_django_user", password="password"
		)
		# Then, create a StravaUser linked to the Django User
		self.strava_user = StravaUser.objects.create(
			user=django_user,
			strava_id=998877,  # Example Strava ID
			_access_token=encrypt_data("dummy_access_token_hr"),
			_refresh_token=encrypt_data("dummy_refresh_token_hr"),
			token_expires_at=timezone.now() + timezone.timedelta(hours=1),
			scope="read,activity:read_all",
		)

		# CustomZonesConfig requires a StravaUser instance
		self.default_zones_data = {
			"Zone 1": [0, 100],
			"Zone 2": [101, 120],
			"Zone 3": [121, 140],
			"Zone 4": [141, 160],
			"Zone 5": [161, 200],
		}
		# Create the CustomZonesConfig instance first
		self.zones_config = CustomZonesConfig.objects.create(
			user=self.strava_user,  # Use the StravaUser instance here
			activity_type="Ride",
		)
		# Then create and associate HeartRateZone objects
		for i, (name, hr_range) in enumerate(self.default_zones_data.items()):
			HeartRateZone.objects.create(
				config=self.zones_config,
				name=name,
				min_hr=hr_range[0],
				max_hr=hr_range[1],
				order=i + 1,
			)

	def test_parse_activity_streams_success(self):
		streams_data = {
			"time": {"data": [0, 1, 2, 3], "original_size": 4, "resolution": "high"},
			"heartrate": {"data": [120, 122, 125, 128], "original_size": 4, "resolution": "high"},
			"distance": {"data": [0.0, 1.0, 2.0, 3.0], "original_size": 4, "resolution": "high"},
			"moving": {
				"data": [True, False, True, False],
				"original_size": 4,
				"resolution": "high",
			},
		}
		time_data, hr_data, distance_data, moving_data = parse_activity_streams(streams_data)
		self.assertEqual(time_data, [0, 1, 2, 3])
		self.assertEqual(hr_data, [120, 122, 125, 128])
		self.assertEqual(distance_data, [0, 1, 2, 3])
		self.assertEqual(moving_data, [True, False, True, False])

	def test_parse_activity_streams_missing_time(self):
		streams_data = {"heartrate": {"data": [120, 122, 125, 128], "original_size": 4}}
		time_data, hr_data, *_ = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)
		self.assertEqual(hr_data, [120, 122, 125, 128])

	def test_parse_activity_streams_missing_heartrate(self):
		streams_data = {"time": {"data": [0, 1, 2, 3], "original_size": 4}}
		time_data, hr_data, *_ = parse_activity_streams(streams_data)
		self.assertEqual(time_data, [0, 1, 2, 3])
		self.assertIsNone(hr_data)

	def test_parse_activity_streams_empty_data_list(self):
		streams_data = {
			"time": {"data": [], "original_size": 0},
			"heartrate": {"data": [120], "original_size": 1},
		}
		time_data, hr_data, *_ = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)  # Empty list treated as invalid/None
		self.assertEqual(hr_data, [120])

		streams_data_hr_empty = {
			"time": {"data": [0, 1], "original_size": 2},
			"heartrate": {"data": [], "original_size": 0},
		}
		time_data, hr_data, *_ = parse_activity_streams(streams_data_hr_empty)
		self.assertEqual(time_data, [0, 1])
		self.assertIsNone(hr_data)

	def test_parse_activity_streams_non_integer_data(self):
		streams_data = {
			"time": {"data": [0, 1, "a", 3], "original_size": 4},
			"heartrate": {"data": [120, 122, 125, 128], "original_size": 4},
		}
		time_data, hr_data, *_ = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)
		self.assertEqual(hr_data, [120, 122, 125, 128])

	def test_parse_activity_streams_none_or_empty_input(self):
		time_data, hr_data, distance_data, moving_data = parse_activity_streams(None)
		self.assertIsNone(time_data)
		self.assertIsNone(hr_data)
		self.assertIsNone(distance_data)
		self.assertIsNone(moving_data)

		time_data, hr_data, distance_data, moving_data = parse_activity_streams({})
		self.assertIsNone(time_data)
		self.assertIsNone(hr_data)
		self.assertIsNone(distance_data)
		self.assertIsNone(moving_data)

	# Tests for determine_hr_zone
	def test_determine_hr_zone_exact_match(self):
		self.assertEqual(determine_hr_zone(110, self.zones_config), "Zone 2")

	def test_determine_hr_zone_lower_boundary(self):
		self.assertEqual(determine_hr_zone(121, self.zones_config), "Zone 3")

	def test_determine_hr_zone_upper_boundary(self):
		self.assertEqual(determine_hr_zone(140, self.zones_config), "Zone 3")

	def test_determine_hr_zone_below_lowest(self):
		# Create config where lowest zone does not start at 0
		zones_data_gapped = {"Zone 1": [50, 100], "Zone 2": [101, 120]}
		gapped_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="TestGapped"
		)
		for i, (name, hr_range) in enumerate(zones_data_gapped.items()):
			HeartRateZone.objects.create(
				config=gapped_config,
				name=name,
				min_hr=hr_range[0],
				max_hr=hr_range[1],
				order=i + 1,
			)
		self.assertIsNone(determine_hr_zone(40, gapped_config))
		self.assertIsNone(determine_hr_zone(-10, gapped_config))  # Test negative HR
		self.assertEqual(
			determine_hr_zone(0, self.zones_config), "Zone 1"
		)  # Original config starts at 0

	def test_determine_hr_zone_above_highest(self):
		self.assertIsNone(determine_hr_zone(210, self.zones_config))

	def test_determine_hr_zone_between_zones(self):
		# With default config, there are no gaps. Test with a gapped config.
		zones_data_gapped = {"Zone 1": [80, 100], "Zone 3": [121, 140]}
		gapped_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="TestAboveGapped"
		)
		for i, (name, hr_range) in enumerate(zones_data_gapped.items()):
			HeartRateZone.objects.create(
				config=gapped_config,
				name=name,
				min_hr=hr_range[0],
				max_hr=hr_range[1],
				order=i + 1,
			)
		self.assertIsNone(determine_hr_zone(110, gapped_config))

	def test_determine_hr_zone_empty_zones_dict(self):
		# This test now means creating a config with NO HeartRateZone objects
		empty_zones_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="Empty"
		)
		self.assertIsNone(determine_hr_zone(130, empty_zones_config))

	def test_determine_hr_zone_malformed_zone_data(self):
		# This test will check how determine_hr_zone handles malformed HeartRateZone objects
		# that might exist in the DB, even if the model's clean() method prevents new ones.
		malformed_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="Malformed"
		)

		HeartRateZone.objects.create(
			config=malformed_config, name="Good Zone", min_hr=60, max_hr=90, order=1
		)
		HeartRateZone.objects.create(
			config=malformed_config, name="Bad MinMax", min_hr=150, max_hr=140, order=2
		)

		# Test with an HR that would fall into the 'Bad MinMax' if it were valid
		# but should be skipped due to min_hr > max_hr check in determine_hr_zone
		self.assertIsNone(determine_hr_zone(145, malformed_config))

		# Test that the good zone is still found
		self.assertEqual(determine_hr_zone(70, malformed_config), "Good Zone")

	def test_determine_hr_zone_unsorted_zones(self):
		unsorted_zones_data = {
			"Zone 5": [161, 180],
			"Zone 1": [0, 100],
			"Zone 3": [121, 140],
			"Zone 4": [141, 160],
			"Zone 2": [101, 120],
		}
		unsorted_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="Unsorted"
		)
		for i, (name, hr_range) in enumerate(unsorted_zones_data.items()):
			HeartRateZone.objects.create(
				config=unsorted_config,
				name=name,
				min_hr=hr_range[0],
				max_hr=hr_range[1],
				order=i + 1,
			)

		self.assertEqual(determine_hr_zone(110, unsorted_config), "Zone 2")
		self.assertEqual(determine_hr_zone(170, unsorted_config), "Zone 5")
		self.assertEqual(determine_hr_zone(50, unsorted_config), "Zone 1")

	def test_calculate_time_in_zones_basic(self):
		# time_data:  [  0,  10,  20,  30,  40,  50,  60,  70]
		# hr_data:    [ 90, 110, 130, 150, 170,  50, 135, 200] # HR at start of segment
		# duration:      10,  10,  10,  10,  10,  10,  10
		# zone for HR: Z1,  Z2,  Z3,  Z4,  Z5, Out,  Z3, Out
		time_data = [0, 10, 20, 30, 40, 50, 60, 70]
		heartrate_data = [90, 110, 130, 150, 170, 50, 135, 200]

		result = calculate_time_in_zones(time_data, heartrate_data, None, None, self.zones_config)
		expected = {
			"Zone 1": 20,
			"Zone 2": 20,
			"Zone 3": 10,
			"Zone 4": 10,
			"Zone 5": 10,
			OUTSIDE_ZONES_KEY: 0,
		}
		self.assertDictEqual(result, expected)

	def test_calculate_time_in_zones_empty_inputs(self):
		base_expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			base_expected[zn_model.name] = 0

		self.assertDictEqual(
			calculate_time_in_zones(None, [100, 120], None, None, self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([0, 10], None, None, None, self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([], [100, 120], None, None, self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([0, 10], [], None, None, self.zones_config), base_expected
		)

	def test_calculate_time_in_zones_no_config_or_empty_zones(self):
		time_data = [0, 10, 20]
		heartrate_data = [100, 120, 130]
		# Case 1: No zones_config provided
		result_no_config = calculate_time_in_zones(time_data, heartrate_data, None, None, None)
		# All time (10s + 10s = 20s) should be 'Outside Defined Zones'
		self.assertDictEqual(result_no_config, {OUTSIDE_ZONES_KEY: 20})

		# Case 2: zones_config exists but has no HeartRateZone objects
		empty_zones_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="TestEmptyZonesCalc"
		)
		result_empty_zones = calculate_time_in_zones(
			time_data, heartrate_data, None, None, empty_zones_config
		)
		self.assertDictEqual(result_empty_zones, {OUTSIDE_ZONES_KEY: 20})

	def test_calculate_time_in_zones_mismatched_lengths(self):
		time_data = [0, 10, 20]
		heartrate_data = [100, 120]  # Length mismatch
		base_expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			base_expected[zn_model.name] = 0
		self.assertDictEqual(
			calculate_time_in_zones(time_data, heartrate_data, None, None, self.zones_config),
			base_expected,
		)

	def test_calculate_time_in_zones_insufficient_data(self):
		base_expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			base_expected[zn_model.name] = 0

		self.assertDictEqual(
			calculate_time_in_zones([0], [100], None, None, self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([], [], None, None, self.zones_config), base_expected
		)

	def test_calculate_time_in_zones_all_outside(self):
		# HR values are consistently below Zone 1 (which starts at 0 for default_zones_data)
		# or above Zone 5 (which ends at 200 for default_zones_data)
		time_data = [0, 10, 20, 30]  # Adjusted for 3 segments, 30s total
		heartrate_data_low = [-10, -5, -20, -15]  # Last element is dummy for length matching
		heartrate_data_high = [210, 220, 205, 215]  # Last element is dummy

		# Create a config where Zone 1 starts higher to make 'below' more distinct
		gapped_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="GappedBelow"
		)
		HeartRateZone.objects.create(
			config=gapped_config, name="ZTest", min_hr=50, max_hr=100, order=1
		)

		result_low_gapped = calculate_time_in_zones(
			time_data, heartrate_data_low, None, None, gapped_config
		)
		# All time (10s * 3 segments = 30s) should be 'Outside Defined Zones'
		self.assertDictEqual(result_low_gapped, {"ZTest": 0, OUTSIDE_ZONES_KEY: 30})

		result_high_default = calculate_time_in_zones(
			time_data, heartrate_data_high, None, None, self.zones_config
		)
		expected_default_all_outside = {OUTSIDE_ZONES_KEY: 30}
		for zn_model in self.zones_config.zones_definition.all():
			expected_default_all_outside[zn_model.name] = 0
		self.assertDictEqual(result_high_default, expected_default_all_outside)

	def test_calculate_time_in_zones_negative_duration(self):
		time_data = [0, 20, 10]  # Unsorted time, results in negative duration for second segment
		heartrate_data = [100, 120, 130]
		# Segment 1 (0-20s), HR 100 (Z1) -> Z1: 20s
		# Segment 2 (20-10s), HR 120 -> Negative duration, skipped.
		result = calculate_time_in_zones(time_data, heartrate_data, None, None, self.zones_config)

		expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			expected[zn_model.name] = 0
		expected["Zone 2"] = 20  # Only the second segment should be counted
		self.assertDictEqual(result, expected)

	@patch("api.hr_processing.logger")
	def test_determine_hr_zone_db_error_on_fetch(self, mock_logger):
		"""Test determine_hr_zone when a DB error occurs fetching zones."""
		mock_zones_config = MagicMock(spec=CustomZonesConfig)
		mock_zones_config.user_id = self.strava_user.strava_id
		mock_zones_config.activity_type = "TestActivityDBError"
		# Configure the mock to raise an exception when .order_by() is called
		mock_zones_config.zones_definition.order_by.side_effect = Exception("Simulated DB error")

		result = determine_hr_zone(150, mock_zones_config)
		self.assertIsNone(result)
		mock_logger.error.assert_called_once()
		err_msg = (
			f"Error accessing or sorting zones for user {self.strava_user.strava_id}, "
			"activity type TestActivityDBError. Error: Simulated DB error"
		)
		self.assertIn(err_msg, mock_logger.error.call_args[0][0])

	@patch("api.hr_processing.logger")
	def test_determine_hr_zone_all_zones_min_greater_than_max_hr(self, mock_logger):
		"""Test determine_hr_zone when all zones have min_hr > max_hr."""
		config_inverted = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="InvertedHRTest"
		)
		HeartRateZone.objects.create(
			config=config_inverted, name="Zone X", min_hr=150, max_hr=140, order=1
		)
		HeartRateZone.objects.create(
			config=config_inverted, name="Zone Y", min_hr=170, max_hr=160, order=2
		)

		result = determine_hr_zone(145, config_inverted)
		self.assertIsNone(result)

	@patch("api.hr_processing.logger")
	def test_calculate_time_in_zones_db_error_on_config_init(self, mock_logger):
		"""Test calculate_time_in_zones when a DB error occurs initializing zone names from config."""  # noqa: E501
		mock_zones_config = MagicMock(spec=CustomZonesConfig)
		mock_zones_config.id = self.zones_config.id  # For logging message
		# Mock the zones_definition relation to raise an exception when .all().order_by() is called
		mock_zones_config.zones_definition.all.return_value.order_by.side_effect = Exception(
			"DB error on config init"
		)

		time_data = [0, 10, 20, 30]
		hr_data = [100, 110, 120, 130]
		distance_data = [0.0, 1.0, 2.0, 3.0]
		moving_data = [False, True, True, True]
		result = calculate_time_in_zones(
			time_data, hr_data, distance_data, moving_data, mock_zones_config
		)

		# Should default to only OUTSIDE_ZONES_KEY and sum all time there
		self.assertEqual(len(result), 1)
		self.assertIn(OUTSIDE_ZONES_KEY, result)
		self.assertEqual(result[OUTSIDE_ZONES_KEY], 30)  # Total duration 0 to 30
		mock_logger.error.assert_called_once()
		err_msg = (
			f"Error accessing zone definitions for config {self.zones_config.id}: DB error on config init. "  # noqa: E501
			"Proceeding as if no zones were defined."
		)
		self.assertIn(err_msg, mock_logger.error.call_args[0][0])

	def test__is_moving(self):
		"""Test the _is_moving_datapoint helper function."""
		# Both streams missing, should default to True
		self.assertTrue(_is_moving_datapoint(None, None, 1.0, 1))
		# One stream missing, should default to True
		self.assertTrue(_is_moving_datapoint([True], None, 1.0, 0))
		self.assertTrue(_is_moving_datapoint(None, [0.0, 1.0], 1.0, 1))

		moving_data = [False, True, False, False, False]
		distance_data = [0.0, 5.0, 5.5, 7.0, 8.0]
		threshold = 1.0

		self.assertTrue(_is_moving_datapoint(moving_data, distance_data, threshold, 1))
		self.assertFalse(_is_moving_datapoint(moving_data, distance_data, threshold, 2))
		self.assertTrue(_is_moving_datapoint(moving_data, distance_data, threshold, 3))
		self.assertFalse(_is_moving_datapoint(moving_data, distance_data, threshold, 4))


class StravaHRWorkerTests(TestCase):
	def setUp(self) -> None:
		self.django_user = get_user_model().objects.create_user(
			username="testuser_zones", password="password123"
		)
		self.strava_user = StravaUser.objects.create(
			user=self.django_user,
			strava_id=112233,
			_access_token=encrypt_data("dummy_access"),
			_refresh_token=encrypt_data("dummy_refresh"),
			token_expires_at=timezone.now() + timezone.timedelta(hours=1),
			scope="read,activity:read_all",
		)
		self.token = Token.objects.create(user=self.django_user)

	@patch("api.worker.StravaApiClient", autospec=True)
	def test_process_user_activities_no_config(self, MockStravaApiClient: MagicMock) -> None:
		"""Test process_user_activities raises ValueError if no default config."""
		# Ensure no default config for the user
		CustomZonesConfig.objects.filter(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		).delete()
		worker = Worker(user_strava_id=self.strava_user.strava_id)
		with self.assertRaisesRegex(
			ValueError, f"Default zones config not found for user {self.strava_user.strava_id}."
		):
			worker.process_user_activities()

	@patch("api.worker.calculate_time_in_zones")
	@patch("api.worker.parse_activity_streams")
	@patch("api.worker.StravaApiClient", autospec=True)
	def test_process_user_activities_success(
		self,
		MockStravaApiClient: MagicMock,
		mock_parse_streams: MagicMock,
		mock_calc_zones: MagicMock,
	) -> None:
		"""Test successful processing of activities with heart rate."""
		# Setup default config
		config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		HeartRateZone.objects.create(config=config, name="Z1", min_hr=0, max_hr=120, order=1)
		HeartRateZone.objects.create(config=config, name="Z2", min_hr=121, max_hr=150, order=2)

		mock_client_instance = MockStravaApiClient.return_value
		mock_activities_summary = [
			{
				"id": "123",
				"start_date": "2024-01-01T10:00:00Z",
				"has_heartrate": True,
				"type": "Run",
			},
			{
				"id": "456",
				"start_date": "2024-01-02T10:00:00Z",
				"has_heartrate": False,
				"type": "Ride",
			},  # No HR
			{
				"id": "789",
				"start_date": "2024-01-03T10:00:00Z",
				"has_heartrate": True,
				"type": "Run",
			},
		]
		mock_client_instance.fetch_strava_activities.return_value = mock_activities_summary

		# Mock stream data for activity 123
		mock_client_instance.fetch_activity_streams.side_effect = (
			lambda activity_id, **kwargs: {
				"heartrate": {"data": [100, 110, 130], "original_size": 3},
				"time": {"data": [0, 10, 20], "original_size": 3},
			}
			if str(activity_id) == "123"
			else {
				"heartrate": {"data": [140, 145], "original_size": 2},
				"time": {"data": [0, 5], "original_size": 2},
			}
			if str(activity_id) == "789"
			else None
		)

		# Mock parse_activity_streams
		mock_parse_streams.side_effect = (
			lambda streams: ([0, 10, 20], [100, 110, 130])
			if streams and streams.get("heartrate", {}).get("data") == [100, 110, 130]
			else ([0, 5], [140, 145])
			if streams and streams.get("heartrate", {}).get("data") == [140, 145]
			else ([], [])
		)

		# Mock calculate_time_in_zones
		mock_calc_zones.side_effect = (
			lambda time_data, hr_data, zones_cfg: {"Z1": 20, "Z2": 10, OUTSIDE_ZONES_KEY: 5}
			if hr_data == [100, 110, 130]
			else {"Z2": 5, OUTSIDE_ZONES_KEY: 0}
			if hr_data == [140, 145]
			else {}
		)

		worker = Worker(user_strava_id=self.strava_user.strava_id)
		with self.assertLogs(worker.logger, level="INFO") as cm:
			worker.process_user_activities()

		self.assertEqual(
			ActivityZoneTimes.objects.count(), 3
		)  # 2 zones for act 123, 1 for act 789
		self.assertTrue(
			ActivityZoneTimes.objects.filter(
				activity_id=123, zone_name="Z1", duration_seconds=20
			).exists()
		)
		self.assertTrue(
			ActivityZoneTimes.objects.filter(
				activity_id=123, zone_name="Z2", duration_seconds=10
			).exists()
		)
		self.assertTrue(
			ActivityZoneTimes.objects.filter(
				activity_id=789, zone_name="Z2", duration_seconds=5
			).exists()
		)
		mock_client_instance.fetch_strava_activities.assert_called_once_with(
			after=None, per_page=10
		)
		self.assertEqual(
			mock_client_instance.fetch_activity_streams.call_count, 2
		)  # Called for 123 and 789
		self.assertTrue(any("No new activities found" not in log_msg for log_msg in cm.output))
		self.assertTrue(
			any(
				"Activity 456 for user 112233" in log_msg and "no heart rate data" in log_msg
				for log_msg in cm.output
			)
		)
		self.assertTrue(
			any(
				"There is 5 s outside any zone for activity 123." in log_msg
				for log_msg in cm.output
			)
		)

	@patch("api.worker.StravaApiClient", autospec=True)
	def test_process_user_activities_no_new_activities(
		self, MockStravaApiClient: MagicMock
	) -> None:
		"""Test process_user_activities logs info when no new activities are found."""
		CustomZonesConfig.objects.create(user=self.strava_user, activity_type=ActivityType.DEFAULT)
		mock_client_instance = MockStravaApiClient.return_value
		mock_client_instance.fetch_strava_activities.return_value = []  # No activities

		worker = Worker(user_strava_id=self.strava_user.strava_id)
		worker.process_user_activities()
		self.assertEqual(ActivityZoneTimes.objects.count(), 0)

	@patch("api.worker.StravaApiClient", autospec=True)
	def test_process_user_activities_fetch_activities_fails(
		self, MockStravaApiClient: MagicMock
	) -> None:
		"""Test handling StravaApiClient failure during fetch_strava_activities."""
		CustomZonesConfig.objects.create(user=self.strava_user, activity_type=ActivityType.DEFAULT)
		mock_client_instance = MockStravaApiClient.return_value
		mock_client_instance.fetch_strava_activities.side_effect = ValueError("API fetch error")

		worker = Worker(user_strava_id=self.strava_user.strava_id)
		with self.assertRaisesRegex(
			ValueError, f"Failed to fetch activities for user {self.strava_user.strava_id}"
		):
			worker.process_user_activities()

	@patch("api.worker.StravaApiClient", autospec=True)
	def test_process_user_activities_fetch_streams_fails(
		self, MockStravaApiClient: MagicMock
	) -> None:
		"""Test handling StravaApiClient failure during fetch_activity_streams."""
		CustomZonesConfig.objects.create(user=self.strava_user, activity_type=ActivityType.DEFAULT)
		mock_client_instance = MockStravaApiClient.return_value
		mock_client_instance.fetch_strava_activities.return_value = [
			{
				"id": "123",
				"start_date": "2024-01-01T10:00:00Z",
				"has_heartrate": True,
				"type": "Run",
			}
		]
		mock_client_instance.fetch_activity_streams.side_effect = ValueError("Stream fetch error")

		worker = Worker(user_strava_id=self.strava_user.strava_id)
		with self.assertLogs(worker.logger, level="ERROR") as cm:
			worker.process_user_activities()

		err_msg = (
			"Failed to fetch or parse streams for activity 123 "
			f"(user {self.strava_user.strava_id}): Stream fetch error"
		)
		self.assertTrue(any(err_msg in log_msg for log_msg in cm.output))
		self.assertEqual(
			ActivityZoneTimes.objects.count(), 0
		)  # No zones should be saved if streams fail

	def test__get_all_user_zone_configs(self) -> None:  # noqa: PLR0915
		worker = Worker(user_strava_id=self.strava_user.strava_id)

		# 1. No configurations exist
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		# _get_default_zones_config will be called internally and return None
		with self.assertLogs(worker.logger, level="INFO") as cm_no_config:
			configs_map = worker._get_all_user_zone_configs()
		self.assertIn(
			f"No explicit DEFAULT config in DB for user {self.strava_user.strava_id}",
			cm_no_config.output[0],
		)
		self.assertEqual(len(configs_map), 0)  # Expect empty map if no default is found/created
		self.assertIsNone(configs_map.get(ActivityType.DEFAULT))  # type: ignore[call-overload]

		# 2. Only DEFAULT config exists
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		default_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		# Add a zone definition to make it a 'complete' config for this test part
		HeartRateZone.objects.create(
			config=default_config, name="Z1", min_hr=60, max_hr=120, order=1
		)
		configs_map = worker._get_all_user_zone_configs()
		self.assertEqual(len(configs_map), 1)
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), default_config)  # type: ignore[call-overload]

		# 3. DEFAULT and RIDE configs exist
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		default_config_2 = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		HeartRateZone.objects.create(
			config=default_config_2, name="Z1", min_hr=60, max_hr=120, order=1
		)
		ride_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RIDE
		)
		HeartRateZone.objects.create(
			config=ride_config, name="Z1-Ride", min_hr=70, max_hr=130, order=1
		)
		configs_map = worker._get_all_user_zone_configs()
		self.assertEqual(len(configs_map), 2)
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), default_config_2)  # type: ignore[call-overload]
		self.assertEqual(configs_map.get(ActivityType.RIDE), ride_config)  # type: ignore[call-overload]

		# 4. DEFAULT, RIDE, and RUN configs exist
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		default_config_3 = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		HeartRateZone.objects.create(
			config=default_config_3, name="Z1", min_hr=60, max_hr=120, order=1
		)
		ride_config_2 = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RIDE
		)
		HeartRateZone.objects.create(
			config=ride_config_2, name="Z1-Ride", min_hr=70, max_hr=130, order=1
		)
		run_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RUN
		)
		HeartRateZone.objects.create(
			config=run_config, name="Z1-Run", min_hr=80, max_hr=140, order=1
		)
		configs_map = worker._get_all_user_zone_configs()
		self.assertEqual(len(configs_map), 3)
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), default_config_3)  # type: ignore[call-overload]
		self.assertEqual(configs_map.get(ActivityType.RIDE), ride_config_2)  # type: ignore[call-overload]
		self.assertEqual(configs_map.get(ActivityType.RUN), run_config)  # type: ignore[call-overload]

		# 5a. Only RIDE config exists (no explicit DEFAULT), _get_default_zones_config returns None
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		ride_only_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RIDE
		)
		HeartRateZone.objects.create(
			config=ride_only_config, name="Z1-Ride", min_hr=70, max_hr=130, order=1
		)
		with patch.object(
			worker, "_get_default_zones_config", return_value=None
		) as mock_get_default:
			with self.assertLogs(worker.logger, level="INFO") as cm_ride_only:
				configs_map = worker._get_all_user_zone_configs()
			mock_get_default.assert_called_once()
		self.assertIn(
			f"No explicit DEFAULT config in DB for user {self.strava_user.strava_id}",
			cm_ride_only.output[0],
		)
		self.assertEqual(len(configs_map), 1)  # Only RIDE config, as DEFAULT was None
		self.assertEqual(configs_map.get(ActivityType.RIDE), ride_only_config)  # type: ignore[call-overload]
		self.assertIsNone(configs_map.get(ActivityType.DEFAULT))  # type: ignore[call-overload]

		# 5b. Only RIDE config exists, _get_default_zones_config returns a new DEFAULT
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		ride_only_config_2 = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RIDE
		)
		HeartRateZone.objects.create(
			config=ride_only_config_2, name="Z1-Ride", min_hr=70, max_hr=130, order=1
		)
		mocked_default_config = CustomZonesConfig(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		# Not saving mocked_default_config to DB, just returning it from mock
		with patch.object(
			worker, "_get_default_zones_config", return_value=mocked_default_config
		) as mock_get_default_2:
			with self.assertLogs(worker.logger, level="INFO") as cm_ride_only_2:
				configs_map = worker._get_all_user_zone_configs()
			mock_get_default_2.assert_called_once()
		self.assertIn(
			f"No explicit DEFAULT config in DB for user {self.strava_user.strava_id}",
			cm_ride_only_2.output[0],
		)
		self.assertEqual(len(configs_map), 2)
		self.assertEqual(configs_map.get(ActivityType.RIDE), ride_only_config_2)  # type: ignore[call-overload]
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), mocked_default_config)  # type: ignore[call-overload]

		# 6. Config with invalid activity_type string in DB
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		default_config_invalid_sibling = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		HeartRateZone.objects.create(
			config=default_config_invalid_sibling, name="Z1", min_hr=60, max_hr=120, order=1
		)
		# Create a config with an invalid type via update to bypass model choices validation
		invalid_config_obj = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="TEMP_VALID"
		)
		CustomZonesConfig.objects.filter(pk=invalid_config_obj.pk).update(
			activity_type="INVALID_TYPE"
		)

		with self.assertLogs(worker.logger, level="ERROR") as cm_invalid_type:
			configs_map = worker._get_all_user_zone_configs()
		self.assertIn(
			f"Invalid activity_type 'INVALID_TYPE' in DB for CustomZonesConfig {invalid_config_obj.pk}",  # noqa: E501
			cm_invalid_type.output[0],
		)
		self.assertEqual(len(configs_map), 1)
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), default_config_invalid_sibling)  # type: ignore[call-overload]

		# 7. Explicit DEFAULT config with no zone definitions
		CustomZonesConfig.objects.filter(user=self.strava_user).delete()
		default_no_zones = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		# No HeartRateZone objects created for default_no_zones
		with self.assertLogs(worker.logger, level="WARNING") as cm_no_zones:
			configs_map = worker._get_all_user_zone_configs()
		self.assertIn(
			f"Explicit default CustomZonesConfig {default_no_zones.pk} for user {self.strava_user.strava_id} has no zone definitions",  # noqa: E501
			cm_no_zones.output[0],
		)
		self.assertEqual(len(configs_map), 1)
		self.assertEqual(configs_map.get(ActivityType.DEFAULT), default_no_zones)  # type: ignore[call-overload]


class ZoneSummaryViewTests(APITestCase):
	def setUp(self) -> None:
		self.django_user = get_user_model().objects.create_user(
			username="testsummaryuser", password="password"
		)
		self.strava_user_id = 304676  # Using a test-specific ID or one from example
		self.strava_user = StravaUser.objects.create(
			user=self.django_user,
			strava_id=self.strava_user_id,
			_access_token=encrypt_data("dummy_access_summary"),
			_refresh_token=encrypt_data("dummy_refresh_summary"),
			token_expires_at=timezone.now() + timedelta(hours=1),
			scope="read,activity:read_all",
		)
		self.token = Token.objects.create(user=self.django_user)
		self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

		# Data for March 2025
		# Week 9 of 2025 is Feb 24 - Mar 2. Activities on Mar 1 & 2 fall in this week and month.
		# Activity 1: March 1, 2025 (Saturday, Week 9)
		activity1_date = datetime(2025, 3, 1, 10, 0, 0, tzinfo=pytz.UTC)
		# Sunday, February 4, 2024 (ISO Week 5)
		activity2_date = datetime(2025, 3, 2, 11, 0, 0, tzinfo=pytz.UTC)

		iso_year, iso_week_jan, _ = activity1_date.isocalendar()
		iso_year_feb, iso_week_feb, _ = activity2_date.isocalendar()

		self.assertEqual(iso_year, 2025)
		self.assertEqual(iso_week_jan, 9)
		self.assertEqual(iso_year_feb, 2025)
		self.assertEqual(iso_week_feb, 9)  # Both should be in week 9

		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=101,
			zone_name="Z1 Endurance",
			duration_seconds=5062,
			activity_date=activity1_date,
		)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=101,
			zone_name="Z2 Moderate",
			duration_seconds=1014,
			activity_date=activity1_date,
		)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=101,
			zone_name="Z3 Tempo",
			duration_seconds=49,
			activity_date=activity1_date,
		)

		# Activity 2: March 2, 2025 (Sunday, Week 9)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=102,
			zone_name="Z4 Threshold",
			duration_seconds=264,
			activity_date=activity2_date,
		)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=102,
			zone_name="Z5 Anaerobic",
			duration_seconds=1662,
			activity_date=activity2_date,
		)

		# Activity 3: March 3, 2025 (Monday, Week 10)
		activity3_date = datetime(2025, 3, 3, 12, 0, 0, tzinfo=pytz.UTC)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=103,
			zone_name="Z1 Endurance",
			duration_seconds=1000,
			activity_date=activity3_date,
		)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=103,
			zone_name="Z2 Moderate",
			duration_seconds=2000,
			activity_date=activity3_date,
		)

		self.default_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		self.dz1 = HeartRateZone.objects.create(
			config=self.default_config, name="Z1 Endurance", min_hr=0, max_hr=120, order=1
		)
		self.dz2 = HeartRateZone.objects.create(
			config=self.default_config, name="Z2 Moderate", min_hr=121, max_hr=140, order=2
		)
		self.dz3 = HeartRateZone.objects.create(
			config=self.default_config, name="Z3 Tempo", min_hr=141, max_hr=160, order=3
		)
		self.dz4 = HeartRateZone.objects.create(
			config=self.default_config, name="Z4 Threshold", min_hr=161, max_hr=180, order=4
		)
		self.dz5 = HeartRateZone.objects.create(
			config=self.default_config, name="Z5 Anaerobic", min_hr=181, max_hr=200, order=5
		)

	def test_get_zone_summary_calculated(self) -> None:
		url = reverse("zone_summary")
		response = self.client.get(url, {"year": 2025, "month": 3})

		self.assertEqual(response.status_code, status.HTTP_200_OK)

		data = response.json()  # Using .json() method for APITestCase response
		self.assertEqual(data["year"], 2025)
		self.assertEqual(data["month"], 3)

		monthly_summary_data = data["monthly_summary"]
		self.assertEqual(monthly_summary_data["period_type"], "MONTHLY")
		self.assertEqual(monthly_summary_data["year"], 2025)
		self.assertEqual(monthly_summary_data["period_index"], 3)
		self.assertEqual(monthly_summary_data["user"], self.strava_user.strava_id)

		expected_monthly_zones = {
			"Z1 Endurance": 5062 + 1000,
			"Z2 Moderate": 1014 + 2000,
			"Z3 Tempo": 49,
			"Z4 Threshold": 264,
			"Z5 Anaerobic": 1662,
		}
		self.assertDictEqual(monthly_summary_data["zone_times_seconds"], expected_monthly_zones)

		# Weekly Summary Assertions
		self.assertTrue(
			len(data["weekly_summaries"]) >= 2, "Should find at least summaries for week 9 and 10"
		)

		week9_summary_data = None
		week10_summary_data = None
		for weekly_summary in data["weekly_summaries"]:
			if weekly_summary["period_index"] == 9 and weekly_summary["year"] == 2025:
				week9_summary_data = weekly_summary
			elif weekly_summary["period_index"] == 10 and weekly_summary["year"] == 2025:
				week10_summary_data = weekly_summary

		self.assertIsNotNone(week9_summary_data, "Week 9 summary not found in response")
		if week9_summary_data:
			self.assertEqual(week9_summary_data["period_type"], "WEEKLY")
			self.assertEqual(week9_summary_data["user"], self.strava_user_id)
			expected_week9_zones = {
				"Z1 Endurance": 5062,
				"Z2 Moderate": 1014,
				"Z3 Tempo": 49,
				"Z4 Threshold": 264,
				"Z5 Anaerobic": 1662,
			}
			self.assertDictEqual(week9_summary_data["zone_times_seconds"], expected_week9_zones)

		self.assertIsNotNone(week10_summary_data, "Week 10 summary not found in response")
		if week10_summary_data:
			self.assertEqual(week10_summary_data["period_type"], "WEEKLY")
			self.assertEqual(week10_summary_data["user"], self.strava_user_id)
			expected_week10_zones = {
				"Z1 Endurance": 1000,
				"Z2 Moderate": 2000,
			}
			self.assertDictEqual(week10_summary_data["zone_times_seconds"], expected_week10_zones)

		# Check database persistence for the specific summaries we expect to be created
		self.assertTrue(
			ZoneSummary.objects.filter(
				user=self.strava_user, period_type="MONTHLY", year=2025, period_index=3
			).exists()
		)
		self.assertTrue(
			ZoneSummary.objects.filter(
				user=self.strava_user, period_type="WEEKLY", year=2025, period_index=9
			).exists()
		)
		self.assertTrue(
			ZoneSummary.objects.filter(
				user=self.strava_user, period_type="WEEKLY", year=2025, period_index=10
			).exists()
		)


class ZoneSummaryModelTests(TestCase):
	def setUp(self) -> None:
		self.user_model = get_user_model()
		self.django_user = self.user_model.objects.create_user(
			username="testdjango_user", password="password123"
		)
		self.strava_user = StravaUser.objects.create(
			strava_id=123456,
			_access_token="test_access_token",
			_refresh_token="test_refresh_token",
			token_expires_at=timezone.now() + timedelta(hours=1),
			user=self.django_user,
			scope="read,activity:read_all",
		)

		self.default_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		self.dz1 = HeartRateZone.objects.create(
			config=self.default_config, name="Z1 Endurance", min_hr=0, max_hr=120, order=1
		)
		self.dz2 = HeartRateZone.objects.create(
			config=self.default_config, name="Z2 Moderate", min_hr=121, max_hr=140, order=2
		)
		self.dz3 = HeartRateZone.objects.create(
			config=self.default_config, name="Z3 Tempo", min_hr=141, max_hr=160, order=3
		)
		self.dz4 = HeartRateZone.objects.create(
			config=self.default_config, name="Z4 Threshold", min_hr=161, max_hr=180, order=4
		)
		self.dz5 = HeartRateZone.objects.create(
			config=self.default_config, name="Z5 Anaerobic", min_hr=181, max_hr=200, order=5
		)

	def test_get_or_create_summary_weekly_with_month_context(self) -> None:
		# Week 5, 2024 spans January and February
		# Monday, January 29, 2024 (ISO Week 5)
		activity_date_jan = datetime(2024, 1, 29, 10, 0, 0, tzinfo=pytz.UTC)
		# Sunday, February 4, 2024 (ISO Week 5)
		activity_date_feb = datetime(2024, 2, 4, 10, 0, 0, tzinfo=pytz.UTC)

		iso_year, iso_week_jan, _ = activity_date_jan.isocalendar()
		iso_year_feb, iso_week_feb, _ = activity_date_feb.isocalendar()

		self.assertEqual(iso_year, 2024)
		self.assertEqual(iso_week_jan, 5)
		self.assertEqual(iso_year_feb, 2024)
		self.assertEqual(iso_week_feb, 5)  # Both should be in week 5

		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=1,
			zone_name="Z1 Jan",
			duration_seconds=100,
			activity_date=activity_date_jan,
		)
		ActivityZoneTimes.objects.create(
			user=self.strava_user,
			activity_id=2,
			zone_name="Z1 Feb",
			duration_seconds=200,
			activity_date=activity_date_feb,
		)

		# Test for January context (Week 5 of 2024, only January activities)
		summary_jan_context, created_jan = ZoneSummary.get_or_create_summary(
			user_profile=self.strava_user,
			period_type=ZoneSummary.PeriodType.WEEKLY,  # type: ignore[arg-type]
			year=iso_year,  # Should be 2024
			period_index=iso_week_jan,  # Should be 5
			current_month_view=1,  # January
		)
		self.assertTrue(created_jan)
		self.assertIsNotNone(summary_jan_context)
		if summary_jan_context:
			self.assertEqual(summary_jan_context.zone_times_seconds, {"Z1 Jan": 100})

		# Test for February context (Week 5 of 2024, only February activities)
		# This call should GET the previously created summary object for week 5, 2024
		# and then RECALCULATE its zone_times_seconds based on the new February context.
		summary_feb_context, created_feb = ZoneSummary.get_or_create_summary(
			user_profile=self.strava_user,
			period_type=ZoneSummary.PeriodType.WEEKLY,  # type: ignore[arg-type]
			year=iso_year,  # Should be 2024
			period_index=iso_week_feb,  # Should be 5
			current_month_view=2,  # February
		)
		self.assertFalse(created_feb, "Second call for same period should fetch, not create.")
		self.assertIsNotNone(summary_feb_context)
		if summary_feb_context:
			self.assertEqual(summary_feb_context.zone_times_seconds, {"Z1 Feb": 200})
			# Ensure it's the same DB object that was updated
			self.assertEqual(summary_jan_context.pk, summary_feb_context.pk)  # type: ignore[union-attr]

		# Final check: what if no month context is given for that week?
		# It should again GET the same summary object and RECALCULATE it to sum both activities.
		summary_no_context, created_no_context = ZoneSummary.get_or_create_summary(
			user_profile=self.strava_user,
			period_type=ZoneSummary.PeriodType.WEEKLY,  # type: ignore[arg-type]
			year=iso_year,
			period_index=iso_week_jan,
			current_month_view=None,  # No specific month context
		)
		self.assertFalse(created_no_context, "Third call for same period should also fetch.")
		self.assertIsNotNone(summary_no_context)
		if summary_no_context:
			self.assertEqual(summary_no_context.zone_times_seconds, {"Z1 Jan": 100, "Z1 Feb": 200})
			# Ensure it's still the same DB object
			self.assertEqual(summary_jan_context.pk, summary_no_context.pk)  # type: ignore[union-attr]


class UserHRZonesDisplayViewTests(TestCase):
	def setUp(self) -> None:
		self.factory = RequestFactory()
		self.user = User.objects.create_user(
			username="testuserdisplayview", password="password", email="test@example.com"
		)
		self.strava_user = StravaUser.objects.create(
			strava_id=1234567,  # Unique Strava ID
			user=self.user,
			access_token="test_access_token",
			refresh_token="test_refresh_token",
			token_expires_at=timezone.now() + timedelta(hours=1),
		)
		# Login the user for views that require authentication via self.client
		self.client.login(username="testuserdisplayview", password="password")

		self.view = UserHRZonesDisplayView()
		self.url = reverse("user_hr_zones_display")

		# Create a default configuration
		self.default_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.DEFAULT
		)
		self.dz1 = HeartRateZone.objects.create(
			config=self.default_config, name="Z1 Default", min_hr=0, max_hr=120, order=1
		)
		self.dz2 = HeartRateZone.objects.create(
			config=self.default_config, name="Z2 Default", min_hr=121, max_hr=140, order=2
		)
		self.dz3 = HeartRateZone.objects.create(
			config=self.default_config, name="Z3 Default", min_hr=141, max_hr=160, order=3
		)

		# Create a running configuration
		self.running_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RUN
		)
		self.rz1 = HeartRateZone.objects.create(
			config=self.running_config, name="Run Z1", min_hr=0, max_hr=110, order=1
		)
		self.rz2 = HeartRateZone.objects.create(
			config=self.running_config, name="Run Z2", min_hr=111, max_hr=130, order=2
		)

		# Create a cycling configuration (initially empty for some tests)
		self.cycling_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type=ActivityType.RIDE
		)

	def _make_post_request_to_view(
		self, user, data: dict, view_instance: UserHRZonesDisplayView
	) -> tuple[HttpRequest, HttpResponse]:
		"""
		Helper to make a POST request directly to the view's post method.
		Returns the request object and the response from the view.
		"""
		request = self.factory.post(self.url, data)
		request.user = user

		# Manually add session and messages support for RequestFactory requests
		if not hasattr(request, "session"):
			engine = import_module(settings.SESSION_ENGINE)
			request.session = engine.SessionStore()

		if not hasattr(request, "_messages"):
			request._messages = storage.default_storage(request)

		response_from_view = view_instance.post(request)
		return request, response_from_view

	def _generate_zone_data_dict(
		self, zone_id: int | None, name: str, min_hr: int, max_hr: int, order: int
	) -> dict[str, str]:
		"""Generates a dictionary for a single zone's data (for internal use in tests)."""
		return {
			"id": str(zone_id) if zone_id else "",
			"name": name,
			"min_hr": str(min_hr),
			"max_hr": str(max_hr),
			"order": str(order),
		}

	def _build_form_data(self, action: str, configs_data_list: list[tuple] | None = None) -> dict:
		"""Builds the flat form data dictionary as expected by the view.

		Additional parameters
		---------------------
		configs_data_list
		        List of tuples: (config_idx, config_id, activity_type_value, zones_list_of_dicts)
		"""
		form_data = {"action": action}
		if configs_data_list:
			for (
				config_idx,
				config_id,
				activity_type_value,
				zones_list_of_dicts,
			) in configs_data_list:
				form_data[f"configs[{config_idx}][id]"] = str(config_id)
				form_data[f"configs[{config_idx}][activity_type]"] = activity_type_value
				for zone_idx, zone_data_dict in enumerate(zones_list_of_dicts):
					for key, value in zone_data_dict.items():
						form_data[f"configs[{config_idx}][zones][{zone_idx}][{key}]"] = value
		return form_data

	def test_get_user_hr_zones_display_authenticated(self) -> None:
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 200)
		self.assertTemplateUsed(response, "api/hr_zone_display.html")
		self.assertIn("user_zone_configurations", response.context)
		self.assertEqual(len(response.context["user_zone_configurations"]), 3)

	def test_save_all_configs_successful_update_and_create(self) -> None:
		default_zones_data = [
			self._generate_zone_data_dict(self.dz1.id, "Z1 Default Updated", 0, 125, 1),
			self._generate_zone_data_dict(self.dz2.id, "Z2 Default Updated", 126, 145, 2),
			self._generate_zone_data_dict(self.dz3.id, "Z3 Default", 146, 165, 3),
			self._generate_zone_data_dict(None, "Z4 Default New", 166, 999, 4),
		]
		running_zones_data = [
			self._generate_zone_data_dict(self.rz1.id, "Run Z1 Updated", 0, 115, 1),
			self._generate_zone_data_dict(None, "Run Z3 New", 116, 135, 2),
		]

		form_data = self._build_form_data(
			action="save_all_zone_configs",
			configs_data_list=[
				(0, self.default_config.id, ActivityType.DEFAULT.value, default_zones_data),  # type: ignore[attr-defined]
				(1, self.running_config.id, ActivityType.RUN.value, running_zones_data),  # type: ignore[attr-defined]
			],
		)

		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertEqual(len(messages), 1)
		self.assertEqual(str(messages[0]), "Heart rate zones saved successfully!")

		self.default_config.refresh_from_db()
		self.assertEqual(self.default_config.zones_definition.count(), 4)
		self.assertEqual(self.default_config.zones_definition.get(order=1).max_hr, 125)
		self.assertEqual(self.default_config.zones_definition.get(order=4).name, "Z4 Default New")

		self.running_config.refresh_from_db()
		self.assertEqual(self.running_config.zones_definition.count(), 2)
		self.assertEqual(
			self.running_config.zones_definition.get(order=1).name, "Z1 Default Updated"
		)
		self.assertEqual(self.running_config.zones_definition.get(order=1).max_hr, 115)
		self.assertEqual(
			self.running_config.zones_definition.get(order=2).name, "Z2 Default Updated"
		)
		self.assertEqual(self.running_config.zones_definition.get(order=2).max_hr, 135)

	def test_save_all_configs_default_zone_name_propagation(self) -> None:
		default_zones_data = [
			self._generate_zone_data_dict(self.dz1.id, "Default Alpha", 0, 120, 1),
			self._generate_zone_data_dict(self.dz2.id, "Default Beta", 121, 140, 2),
		]
		running_zones_data = [
			self._generate_zone_data_dict(self.rz1.id, "Original Run Z1", 0, 110, 1),
			self._generate_zone_data_dict(self.rz2.id, "Original Run Z2", 111, 130, 2),
		]
		form_data = self._build_form_data(
			action="save_all_zone_configs",
			configs_data_list=[
				(0, self.default_config.id, ActivityType.DEFAULT.value, default_zones_data),  # type: ignore[attr-defined]
				(1, self.running_config.id, ActivityType.RUN.value, running_zones_data),  # type: ignore[attr-defined]
			],
		)
		_request_obj, _response = self._make_post_request_to_view(self.user, form_data, self.view)

		self.running_config.refresh_from_db()
		self.assertEqual(self.running_config.zones_definition.get(order=1).name, "Default Alpha")
		self.assertEqual(self.running_config.zones_definition.get(order=2).name, "Default Beta")
		self.default_config.refresh_from_db()
		self.assertEqual(self.default_config.zones_definition.get(order=1).name, "Default Alpha")

	def test_save_all_configs_delete_zones(self) -> None:
		default_zones_data = [
			self._generate_zone_data_dict(self.dz1.id, "Z1 Default Only", 0, 150, 1),
		]
		form_data = self._build_form_data(
			action="save_all_zone_configs",
			configs_data_list=[
				(
					0,
					self.default_config.id,
					ActivityType.DEFAULT.value,  # type: ignore[attr-defined]
					default_zones_data,
				),  # Changed to DEFAULT from RUN
			],
		)
		_request_obj, _response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.default_config.refresh_from_db()
		self.assertEqual(self.default_config.zones_definition.count(), 1)
		self.assertEqual(self.default_config.zones_definition.first().name, "Z1 Default Only")

	def test_save_all_configs_open_ended_max_hr(self):
		default_zones_data = [
			self._generate_zone_data_dict(self.dz1.id, "Z1", 0, 120, 1),
			self._generate_zone_data_dict(self.dz2.id, "Z2 Open", 121, "open", 2),
			self._generate_zone_data_dict(self.dz3.id, "Z3 Empty", 141, "", 3),
		]
		form_data = self._build_form_data(
			action="save_all_zone_configs",
			configs_data_list=[
				(0, self.default_config.id, ActivityType.DEFAULT.value, default_zones_data),
			],
		)
		request_obj, response = self._make_post_request_to_view(
			self.user, form_data, self.view
		)  # Modified
		self.default_config.refresh_from_db()
		self.assertEqual(self.default_config.zones_definition.get(order=2).max_hr, 220)
		self.assertEqual(self.default_config.zones_definition.get(order=3).max_hr, 220)

	def test_add_default_zones_to_empty_config(self) -> None:
		self.assertTrue(self.cycling_config.zones_definition.count() == 0)
		form_data = {"action": f"add_default_zones_to_{self.cycling_config.id}"}

		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertTrue(any("Default zones added" in str(m) for m in messages))

		self.cycling_config.refresh_from_db()
		self.assertEqual(
			self.cycling_config.zones_definition.count(),
			self.default_config.zones_definition.count(),
		)
		cycled_z1 = self.cycling_config.zones_definition.get(order=1)
		default_z1 = self.default_config.zones_definition.get(order=1)
		self.assertEqual(cycled_z1.name, default_z1.name)
		self.assertEqual(cycled_z1.min_hr, default_z1.min_hr)
		self.assertEqual(cycled_z1.max_hr, default_z1.max_hr)

	def test_add_new_activity_config_copies_defaults(self) -> None:
		# Set up DB
		activity_to_add = ActivityType.RIDE.value  # type: ignore[attr-defined]
		CustomZonesConfig.objects.filter(
			user=self.strava_user, activity_type=activity_to_add
		).delete()
		self.assertFalse(
			CustomZonesConfig.objects.filter(
				user=self.strava_user, activity_type=activity_to_add
			).exists()
		)

		form_data = {
			"action": "add_new_activity_config",
			"new_activity_type": activity_to_add,
		}
		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertTrue(
			any(
				f"Configuration for {ActivityType(activity_to_add).label} added" in str(m)
				for m in messages
			)
		)

		new_config = CustomZonesConfig.objects.get(
			user=self.strava_user, activity_type=activity_to_add
		)
		self.assertIsNotNone(new_config)
		self.assertEqual(
			new_config.zones_definition.count(), self.default_config.zones_definition.count()
		)
		self.assertEqual(
			new_config.zones_definition.get(order=1).name,
			self.default_config.zones_definition.get(order=1).name,
		)

	def test_add_new_activity_config_already_exists(self) -> None:
		activity_to_add = ActivityType.RUN.value  # type: ignore[attr-defined]
		form_data = {
			"action": "add_new_activity_config",
			"new_activity_type": activity_to_add,
		}
		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertTrue(any("already exists" in str(m) for m in messages))
		self.assertEqual(
			CustomZonesConfig.objects.filter(
				user=self.strava_user, activity_type=activity_to_add
			).count(),
			1,
		)

	def test_add_new_activity_config_invalid_type(self) -> None:
		form_data = {
			"action": "add_new_activity_config",
			"new_activity_type": "INVALID_TYPE",
		}
		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertTrue(any("Invalid activity type selected" in str(m) for m in messages))

	def test_add_new_activity_config_add_default_again(self) -> None:
		form_data = {
			"action": "add_new_activity_config",
			"new_activity_type": ActivityType.DEFAULT.value,  # type: ignore[attr-defined]
		}
		request_obj, response = self._make_post_request_to_view(self.user, form_data, self.view)
		self.assertEqual(response.status_code, 302)
		messages = list(get_messages(request_obj))
		self.assertTrue(any("DEFAULT cannot be added again" in str(m) for m in messages))

	def test_delete_activity_config(self) -> None:
		"""Test deleting an activity-specific HR zone configuration."""
		self.client.login(username="testuserdisplayview", password="password")

		# Ensure the running config exists before deletion
		self.assertTrue(CustomZonesConfig.objects.filter(id=self.running_config.id).exists())
		running_config_id = self.running_config.id
		initial_config_count = CustomZonesConfig.objects.filter(user=self.strava_user).count()

		response = self.client.post(
			self.url,
			{
				"action": "delete_activity_config",
				"config_id_to_delete": str(running_config_id),
			},
		)

		self.assertEqual(response.status_code, 302)  # Should redirect

		# Check that the running config is deleted
		self.assertFalse(CustomZonesConfig.objects.filter(id=running_config_id).exists())

		# Check that other configs (e.g., default) still exist
		self.assertTrue(CustomZonesConfig.objects.filter(id=self.default_config.id).exists())
		self.assertTrue(CustomZonesConfig.objects.filter(id=self.cycling_config.id).exists())

		# Check that the total count of configs for the user has decreased by one
		self.assertEqual(
			CustomZonesConfig.objects.filter(user=self.strava_user).count(),
			initial_config_count - 1,
		)

		# Check for success message
		messages = list(get_messages(response.wsgi_request))
		self.assertEqual(len(messages), 1)
		# The display name for ActivityType.RUN is 'Run'
		self.assertEqual(str(messages[0]), "Successfully deleted the 'Run' configuration.")

	def test_delete_default_activity_config_attempt(self) -> None:
		"""Test attempting to delete the DEFAULT HR zone configuration."""
		self.client.login(username="testuserdisplayview", password="password")

		default_config_id = self.default_config.id
		initial_config_count = CustomZonesConfig.objects.filter(user=self.strava_user).count()

		response = self.client.post(
			self.url,
			{
				"action": "delete_activity_config",
				"config_id_to_delete": str(default_config_id),
			},
		)

		self.assertEqual(response.status_code, 302)  # Should redirect

		# Check that the default config is NOT deleted
		self.assertTrue(CustomZonesConfig.objects.filter(id=default_config_id).exists())
		self.assertEqual(
			CustomZonesConfig.objects.filter(user=self.strava_user).count(),
			initial_config_count,  # Count should remain the same
		)

		# Check for error message
		messages = list(get_messages(response.wsgi_request))
		self.assertEqual(len(messages), 1)
		self.assertEqual(str(messages[0]), "The 'Default' configuration cannot be deleted.")


class SchedulerTests(TestCase):
	"""Tests for the scheduler functionality."""

	@patch("api.scheduler.BackgroundScheduler")
	def test_start_scheduler(self, mock_background_scheduler: MagicMock) -> None:
		"""Test that the scheduler starts and adds the job correctly."""
		mock_scheduler_instance = MagicMock()
		mock_background_scheduler.return_value = mock_scheduler_instance

		from api.scheduler import process_activity_queue, start_scheduler

		start_scheduler()

		# Assert
		mock_background_scheduler.assert_called_once()
		mock_scheduler_instance.add_job.assert_called_once_with(
			process_activity_queue, "interval", minutes=1
		)
		mock_scheduler_instance.start.assert_called_once()
