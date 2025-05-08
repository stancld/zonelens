from __future__ import annotations

from django.urls import path

from api import views

urlpatterns = [
	path("profile/", views.user_profile, name="user_profile"),
	path("auth/strava/", views.strava_authorize, name="strava_authorize"),
	path("auth/strava/callback/", views.strava_callback, name="strava_callback"),
	path(
		"settings/custom-zones/",
		views.CustomZonesSettingsView.as_view(),
		name="custom_zones_settings",
	),
	path("process-activities/", views.ProcessActivitiesView.as_view(), name="process_activities"),
]
