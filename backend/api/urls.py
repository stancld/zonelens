from __future__ import annotations

from django.urls import path

from api import views

urlpatterns = [
	path("profile/", views.user_profile, name="user_profile"),
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
		"user/hr-zone-status/",
		views.UserHRZoneStatusView.as_view(),
		name="user_hr_zone_status",
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
