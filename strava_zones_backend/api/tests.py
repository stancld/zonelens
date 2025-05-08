from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import requests_mock
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.hr_processing import (
	OUTSIDE_ZONES_KEY,
	calculate_time_in_zones,
	determine_hr_zone,
	parse_activity_streams,
)
from api.models import CustomZonesConfig, HeartRateZone, StravaUser, ZoneSummary
from api.strava_client import (
	STRAVA_API_ACTIVITIES_URL,
	STRAVA_API_MAX_PER_PAGE,
	STRAVA_API_STREAMS_URL_TEMPLATE,
	STRAVA_TOKEN_URL,
	StravaApiClient,
)
from api.utils import decrypt_data, encrypt_data


class InitialMigrationTests(TestCase):
	@property
	def user(self) -> StravaUser:
		return StravaUser.objects.create(
			strava_id=12345,
			_access_token="dummy_encrypted_token",  # Assuming encrypt_data handles string->bytes
			_refresh_token="dummy_encrypted_refresh",
			token_expires_at=datetime.now(),  # Use correct field name
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
			user=self.user,
			activity_type=CustomZonesConfig.ActivityType.RUN,
		)
		self.assertIsNotNone(zone_config.pk)
		self.assertEqual(zone_config.activity_type, CustomZonesConfig.ActivityType.RUN)

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
		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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
		self.url = reverse("zone_settings_list_create")
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
		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_get_zone_settings_authenticated_empty(self) -> None:
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.data, [])

	def test_post_zone_settings_unauthenticated(self) -> None:
		self.client.credentials()  # Clear credentials
		response = self.client.post(self.url, self.sample_payload, format="json")
		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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
		expected_params = {"keys": "heartrate,time", "key_by_type": "true"}

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
		}
		time_data, hr_data = parse_activity_streams(streams_data)
		self.assertEqual(time_data, [0, 1, 2, 3])
		self.assertEqual(hr_data, [120, 122, 125, 128])

	def test_parse_activity_streams_missing_time(self):
		streams_data = {"heartrate": {"data": [120, 122, 125, 128], "original_size": 4}}
		time_data, hr_data = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)
		self.assertEqual(hr_data, [120, 122, 125, 128])

	def test_parse_activity_streams_missing_heartrate(self):
		streams_data = {"time": {"data": [0, 1, 2, 3], "original_size": 4}}
		time_data, hr_data = parse_activity_streams(streams_data)
		self.assertEqual(time_data, [0, 1, 2, 3])
		self.assertIsNone(hr_data)

	def test_parse_activity_streams_empty_data_list(self):
		streams_data = {
			"time": {"data": [], "original_size": 0},
			"heartrate": {"data": [120], "original_size": 1},
		}
		time_data, hr_data = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)  # Empty list treated as invalid/None
		self.assertEqual(hr_data, [120])

		streams_data_hr_empty = {
			"time": {"data": [0, 1], "original_size": 2},
			"heartrate": {"data": [], "original_size": 0},
		}
		time_data, hr_data = parse_activity_streams(streams_data_hr_empty)
		self.assertEqual(time_data, [0, 1])
		self.assertIsNone(hr_data)

	def test_parse_activity_streams_non_integer_data(self):
		streams_data = {
			"time": {"data": [0, 1, "a", 3], "original_size": 4},
			"heartrate": {"data": [120, 122, 125, 128], "original_size": 4},
		}
		time_data, hr_data = parse_activity_streams(streams_data)
		self.assertIsNone(time_data)
		self.assertEqual(hr_data, [120, 122, 125, 128])

	def test_parse_activity_streams_mismatched_lengths(self):
		# Should still parse but log a warning (tested manually)
		streams_data = {
			"time": {"data": [0, 1, 2], "original_size": 3},
			"heartrate": {"data": [120, 122, 125, 128], "original_size": 4},
		}
		with self.assertLogs(level="WARNING") as log:
			time_data, hr_data = parse_activity_streams(streams_data)
			self.assertEqual(time_data, [0, 1, 2])
			self.assertEqual(hr_data, [120, 122, 125, 128])
			self.assertTrue(any("different lengths" in str(message) for message in log.output))

	def test_parse_activity_streams_none_or_empty_input(self):
		time_data, hr_data = parse_activity_streams(None)
		self.assertIsNone(time_data)
		self.assertIsNone(hr_data)

		time_data, hr_data = parse_activity_streams({})
		self.assertIsNone(time_data)
		self.assertIsNone(hr_data)

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

	# --- Tests for calculate_time_in_zones ---
	def test_calculate_time_in_zones_basic(self):
		# time_data:  [  0,  10,  20,  30,  40,  50,  60,  70]
		# hr_data:    [ 90, 110, 130, 150, 170,  50, 135, 200] # HR at start of segment
		# duration:      10,  10,  10,  10,  10,  10,  10
		# zone for HR: Z1,  Z2,  Z3,  Z4,  Z5, Out,  Z3, Out
		time_data = [0, 10, 20, 30, 40, 50, 60, 70]
		heartrate_data = [90, 110, 130, 150, 170, 50, 135, 200]
		# heartrate_data is for HR at the start of the segment, so one less element than time_data
		# For calculate_time_in_zones, we expect len(time_data) == len(heartrate_data) usually,
		# where heartrate_data[i] is the HR for the segment time_data[i] to time_data[i+1].
		# Let's adjust to match typical Strava stream structure where len(time) == len(hr)
		heartrate_data = [
			90,
			110,
			130,
			150,
			170,
			50,
			135,
			200,
		]  # Last HR won't start a new segment

		# Expected distribution based on self.default_zones_data for self.zones_config:
		# Z1 (0-100):   90 (10s)
		# Z2 (101-120): 110 (10s)
		# Z3 (121-140): 130 (10s), 135 (10s) = 20s
		# Z4 (141-160): 150 (10s)
		# Z5 (161-200): 170 (10s)
		# Outside:      50 (10s) (HR 200 is for the last data point, not a segment start)

		# The function uses heartrate_data[i] for the segment time_data[i] to time_data[i+1]
		# So, for 7 segments:
		# Segment 0 (0-10s), HR 90 (Z1) -> Z1: 10s
		# Segment 1 (10-20s), HR 110 (Z2) -> Z2: 10s
		# Segment 2 (20-30s), HR 130 (Z3) -> Z3: 10s
		# Segment 3 (30-40s), HR 150 (Z4) -> Z4: 10s
		# Segment 4 (40-50s), HR 170 (Z5) -> Z5: 10s
		# Segment 5 (50-60s), HR 50 (Z1, as 0-100) -> Z1: 10s + 10s = 20s
		# Segment 6 (60-70s), HR 135 (Z3) -> Z3: 10s + 10s = 20s
		result = calculate_time_in_zones(time_data, heartrate_data, self.zones_config)
		expected = {
			"Zone 1": 20,  # 90 for 10s, 50 for 10s
			"Zone 2": 10,
			"Zone 3": 20,  # 130 for 10s, 135 for 10s
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
			calculate_time_in_zones(None, [100, 120], self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([0, 10], None, self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([], [100, 120], self.zones_config), base_expected
		)
		self.assertDictEqual(
			calculate_time_in_zones([0, 10], [], self.zones_config), base_expected
		)

	def test_calculate_time_in_zones_no_config_or_empty_zones(self):
		time_data = [0, 10, 20]
		heartrate_data = [100, 120, 130]
		# Case 1: No zones_config provided
		result_no_config = calculate_time_in_zones(time_data, heartrate_data, None)
		# All time (10s + 10s = 20s) should be 'Outside Defined Zones'
		self.assertDictEqual(result_no_config, {OUTSIDE_ZONES_KEY: 20})

		# Case 2: zones_config exists but has no HeartRateZone objects
		empty_zones_config = CustomZonesConfig.objects.create(
			user=self.strava_user, activity_type="TestEmptyZonesCalc"
		)
		result_empty_zones = calculate_time_in_zones(time_data, heartrate_data, empty_zones_config)
		self.assertDictEqual(result_empty_zones, {OUTSIDE_ZONES_KEY: 20})

	def test_calculate_time_in_zones_mismatched_lengths(self):
		time_data = [0, 10, 20]
		heartrate_data = [100, 120]  # Length mismatch
		base_expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			base_expected[zn_model.name] = 0
		self.assertDictEqual(
			calculate_time_in_zones(time_data, heartrate_data, self.zones_config), base_expected
		)

	def test_calculate_time_in_zones_insufficient_data(self):
		base_expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			base_expected[zn_model.name] = 0

		self.assertDictEqual(calculate_time_in_zones([0], [100], self.zones_config), base_expected)
		self.assertDictEqual(calculate_time_in_zones([], [], self.zones_config), base_expected)

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

		result_low_gapped = calculate_time_in_zones(time_data, heartrate_data_low, gapped_config)
		# All time (10s * 3 segments = 30s) should be 'Outside Defined Zones'
		self.assertDictEqual(result_low_gapped, {"ZTest": 0, OUTSIDE_ZONES_KEY: 30})

		result_high_default = calculate_time_in_zones(
			time_data, heartrate_data_high, self.zones_config
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
		result = calculate_time_in_zones(time_data, heartrate_data, self.zones_config)

		expected = {OUTSIDE_ZONES_KEY: 0}
		for zn_model in self.zones_config.zones_definition.all():
			expected[zn_model.name] = 0
		expected["Zone 1"] = 20  # Only the first segment should be counted
		self.assertDictEqual(result, expected)


class GPXParsingTests(TestCase):
	# Add your tests here
	pass
