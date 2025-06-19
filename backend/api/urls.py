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

from django.urls import path

from api import views

urlpatterns = [
	path("profile/", views.user_profile, name="user_profile"),
	path("profile/sync_status", views.sync_status, name="sync_status"),
	path("auth/strava/", views.strava_authorize, name="strava_authorize"),
	path("auth/strava/callback/", views.strava_callback, name="strava_callback"),
	path(
		"settings/custom-zones/<uuid:pk>/",
		views.CustomZonesSettingsDetailView.as_view(),
		name="custom_zones_settings_detail",
	),
	path(
		"settings/custom-zones/",
		views.CustomZonesSettingsView.as_view(),
		name="custom_zones_settings",
	),
	path(
		"strava/sync-activities/",
		views.ProcessActivitiesView.as_view(),
		name="strava_sync_activities",
	),
	path(
		"zones/",
		views.ZoneSummaryView.as_view(),
		name="zone_summary",
	),
	path(
		"fetch-strava-hr-zones/",
		views.FetchStravaHRZonesView.as_view(),
		name="fetch_strava_hr_zones",
	),
	path(
		"user/hr-zones/",
		views.UserHRZonesDisplayView.as_view(),
		name="user_hr_zones_display",
	),
	path(
		"strava/webhook/",
		views.StravaWebhookAPIView.as_view(),
		name="strava_webhook",
	),
]
