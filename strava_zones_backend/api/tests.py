from __future__ import annotations

import json
from datetime import datetime

from django.test import TestCase

from api.models import CustomZonesConfig, StravaUser, ZoneSummary


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
			# Use actual field names from the model
			_access_token="dummy_encrypted_token",  # Assuming encrypt_data handles string->bytes
			_refresh_token="dummy_encrypted_refresh",
			token_expires_at=datetime.now(),  # Use correct field name
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
			period_type=ZoneSummary.PeriodType.WEEKLY,  # Use correct field
			year=2025,
			period_index=18,  # Use correct field (e.g., week number)
			zone_times_seconds=json.dumps(
				{  # Use correct field
					"Zone 1": 3600,
					"Zone 2": 1800,
				}
			),
		)
		self.assertIsNotNone(zone_summary.pk)
		self.assertEqual(zone_summary.year, 2025)
		self.assertEqual(zone_summary.period_type, ZoneSummary.PeriodType.WEEKLY)
		self.assertEqual(zone_summary.period_index, 18)
