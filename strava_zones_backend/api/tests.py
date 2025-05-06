from __future__ import annotations

import json
from datetime import datetime

import requests_mock
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from api.models import CustomZonesConfig, StravaUser, ZoneSummary
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
			strava_id=98765, user=user, token_expires_at=timezone.now()
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
