from __future__ import annotations

import calendar
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import (
	HttpRequest,
	HttpResponse,
	HttpResponseBadRequest,
	HttpResponseRedirect,
)
from django.shortcuts import render
from django.utils import timezone
from django.views.generic import TemplateView
from rest_framework import generics, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import ActivityType, CustomZonesConfig, HeartRateZone, StravaUser, ZoneSummary
from api.serializers import CustomZonesConfigSerializer, ZoneSummarySerializer
from api.utils import encrypt_data
from api.worker import StravaHRWorker

if TYPE_CHECKING:
	from typing import Any, TypedDict

	from django.db.models import QuerySet
	from rest_framework.request import Request

	from api.types import Secret

	class TokenPayload(TypedDict):
		client_id: str
		client_secret: Secret
		code: str
		grant_type: str


logger = logging.getLogger(__name__)

User = get_user_model()

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def strava_authorize(request: HttpRequest) -> HttpResponseRedirect:
	"""Redirects the user to Strava's authorization page."""
	scopes = "read,activity:read_all,profile:read_all"
	client_id = settings.STRAVA_CLIENT_ID
	redirect_uri = f"{request.scheme}://localhost:8000/api/auth/strava/callback"

	params = {
		"client_id": client_id,
		"redirect_uri": redirect_uri,
		"response_type": "code",
		"approval_prompt": "auto",
		"scope": scopes,
	}

	return HttpResponseRedirect(f"{STRAVA_AUTH_URL}?{urlencode(params)}")


def strava_callback(request: HttpRequest) -> HttpResponse:
	"""Handles the callback from Strava, exchanges code for tokens."""
	if error := request.GET.get("error"):
		return HttpResponse(f"Error received from Strava: {error}", status=400)

	if not (code := request.GET.get("code")):
		return HttpResponse("Authorization code not found in callback.", status=400)
	try:
		response = requests.post(STRAVA_TOKEN_URL, data=_generate_token_payload(code), timeout=10)

		if response.status_code >= 400:
			return HttpResponse("Failed to authenticate with Strava.", status=400)

		token_data = response.json()

		athlete_info = token_data.get("athlete", {})
		# Brave browser returns id as a tuple
		strava_id = athlete_info.get("id")
		if isinstance(strava_id, tuple):
			strava_id = strava_id[0]

		if not strava_id:
			logger.error("Strava ID not found in token response.")
			return HttpResponseBadRequest("Could not retrieve Strava user ID.")

		user = _get_user(
			username=f"strava_{strava_id}",
			strava_id=strava_id,
			athlete_info=athlete_info,
			token_data=token_data,
			request=request,
		)

		# Log in the user to establish a session (necessary to store cookies for session auth)
		login(request, user)

		drf_token, _ = Token.objects.get_or_create(user=user)

		context = {"token": drf_token.key, "frontend_redirect_url": "/"}
		return render(request, "api/auth_callback.html", context)
	except requests.exceptions.HTTPError:
		return HttpResponse("Failed to authenticate with Strava (HTTPError).", status=400)
	except requests.exceptions.RequestException:
		return HttpResponse("Error connecting to Strava. Please try again later.", status=503)
	except Exception:
		return HttpResponse("An unexpected server error occurred.", status=500)


def _get_user(
	username: str, strava_id: int, athlete_info: dict, token_data: dict, request: HttpRequest
) -> User:  # type: ignore[valid-type]
	user, _user_created = get_user_model().objects.get_or_create(
		username=username,
		defaults={
			"first_name": athlete_info.get("firstname", ""),
			"last_name": athlete_info.get("lastname", ""),
			"email": athlete_info.get("email", ""),
			"last_login": timezone.now(),
		},
	)

	_strava_user, _strava_user_created = StravaUser.objects.update_or_create(
		strava_id=strava_id,
		defaults={
			"user": user,
			"access_token": encrypt_data(token_data["access_token"]),
			"refresh_token": encrypt_data(token_data["refresh_token"]),
			"token_expires_at": timezone.make_aware(
				timezone.datetime.fromtimestamp(token_data["expires_at"])
			),
			"scope": request.GET.get("scope", ""),
		},
	)
	return user


def _generate_token_payload(code: str) -> dict:
	return {
		"client_id": settings.STRAVA_CLIENT_ID,
		"client_secret": settings.STRAVA_CLIENT_SECRET,
		"code": code,
		"grant_type": "authorization_code",
	}


def index_view(request: HttpRequest) -> HttpResponse:
	"""Serves the main index.html template."""
	return render(request, "index.html")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_profile(request: Request) -> Response:
	"""Returns basic profile info for the authenticated user."""
	user = request.user
	api_token = None
	if request.auth:
		api_token = request.auth.key

	return Response(
		{
			"username": user.username,
			"first_name": user.first_name,
			"last_name": user.last_name,
			"strava_id": user.strava_profile.strava_id
			if hasattr(user, "strava_profile")
			else None,
			"api_token": api_token,
		}
	)


class CustomZonesSettingsView(generics.ListCreateAPIView):
	"""Retrieve or create custom zone configurations for the authenticated user."""

	serializer_class = CustomZonesConfigSerializer
	permission_classes = [IsAuthenticated]

	def get_queryset(self) -> QuerySet[CustomZonesConfig]:
		"""Return a list of all custom zone configs for the authenticated user."""
		user = self.request.user
		if hasattr(user, "strava_profile") and user.strava_profile:
			return CustomZonesConfig.objects.filter(user=user.strava_profile).order_by(
				"activity_type"
			)
		return CustomZonesConfig.objects.none()


class CustomZonesSettingsDetailView(generics.RetrieveUpdateDestroyAPIView):
	"""Retrieve, update, or delete a specific custom zone configuration."""

	serializer_class = CustomZonesConfigSerializer
	permission_classes = [IsAuthenticated]
	lookup_field = "pk"

	def get_queryset(self) -> QuerySet[CustomZonesConfig]:
		"""Ensure users can only access their own configurations."""
		user = self.request.user
		if hasattr(user, "strava_profile") and user.strava_profile:
			return CustomZonesConfig.objects.filter(user=user.strava_profile)
		return CustomZonesConfig.objects.none()


class ProcessActivitiesView(APIView):
	"""View to trigger Strava activities syncing and processing for a user."""

	permission_classes = [IsAuthenticated]

	def post(self, request: Request) -> Response:
		try:
			strava_user_profile = request.user.strava_profile
			user_strava_id = strava_user_profile.strava_id
		except AttributeError:
			logger.error(f"User {request.user.username} does not have a Strava profile linked.")
			return Response(
				{"error": "Strava profile not found for this user."},
				status=status.HTTP_400_BAD_REQUEST,
			)

		after_timestamp_unix: int | None = None
		if after_timestamp_iso_str := request.data.get("after_timestamp"):
			try:
				# Parse ISO 8601 string to datetime object
				dt_object = datetime.fromisoformat(after_timestamp_iso_str.replace("Z", "+00:00"))
				# Ensure it's timezone-aware (UTC if no offset specified or 'Z' was used)
				if dt_object.tzinfo is None or dt_object.tzinfo.utcoffset(dt_object) is None:
					dt_object = dt_object.replace(tzinfo=timezone.utc)
				# Convert to Unix timestamp (integer seconds)
				after_timestamp_unix = int(dt_object.timestamp())
			except ValueError:
				return Response(
					{
						"error": "Invalid after_timestamp format. "
						"Expected ISO 8601 string (e.g., YYYY-MM-DDTHH:MM:SSZ)."
					},
					status=status.HTTP_400_BAD_REQUEST,
				)

		try:
			worker = StravaHRWorker(user_strava_id=user_strava_id)
			worker.process_user_activities(after_timestamp=after_timestamp_unix)
			return Response(
				{"message": f"Successfully processed activities for user {user_strava_id}"},
				status=status.HTTP_200_OK,
			)
		except ValueError as e:
			return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
		except Exception as e:
			logger.exception(f"Error processing activities for user {user_strava_id}: {e}")
			return Response(
				{"error": "An unexpected error occurred during processing."},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR,
			)


class ZoneSummaryView(APIView):
	"""View to retrieve aggregated zone summary data for a user."""

	permission_classes = [IsAuthenticated]

	def get(self, request: HttpRequest) -> Response:
		if not (year_str := request.query_params.get("year")) or not (
			month_str := request.query_params.get("month")
		):
			return Response(
				{"error": "'year' and 'month' query parameters are required."},
				status=status.HTTP_400_BAD_REQUEST,
			)

		try:
			year = int(year_str)
			month = int(month_str)
		except ValueError:
			return Response(
				{"error": "'year' and 'month' must be valid integers."},
				status=status.HTTP_400_BAD_REQUEST,
			)

		if not (1 <= month <= 12):
			return Response(
				{"error": "'month' must be between 1 and 12."},
				status=status.HTTP_400_BAD_REQUEST,
			)
		if not (2000 <= year <= datetime.now().year + 1):
			return Response(
				{"error": f"'year' must be between 2000 and {datetime.now().year + 1}."},
				status=status.HTTP_400_BAD_REQUEST,
			)

		user_profile = request.user.strava_profile

		# Fetch monthly summary
		monthly_summary_qs, _created = ZoneSummary.get_or_create_summary(
			user_profile=user_profile,
			period_type=ZoneSummary.PeriodType.MONTHLY,  # type: ignore[arg-type]
			year=year,
			period_index=month,
		)
		monthly_serializer = ZoneSummarySerializer(monthly_summary_qs, many=False)

		# Fetch weekly summaries
		weekly_summaries = []
		for week in sorted(self._determine_weeks_in_month(year, month)):
			weekly_summary_qs, _created = ZoneSummary.get_or_create_summary(
				user_profile=user_profile,
				period_type=ZoneSummary.PeriodType.WEEKLY,  # type: ignore[arg-type]
				year=year,
				period_index=week,
				current_month_view=month,
			)
			if weekly_summary_qs:
				weekly_summaries.append(weekly_summary_qs)

		weekly_serializer = ZoneSummarySerializer(weekly_summaries, many=True)

		return Response(
			{
				"message": "Zone summary data retrieved successfully.",
				"year": year,
				"month": month,
				"monthly_summary": monthly_serializer.data,
				"weekly_summaries": weekly_serializer.data,
			},
			status=status.HTTP_200_OK,
		)

	@staticmethod
	def _determine_weeks_in_month(year: int, month: int) -> list[int]:
		weeks_in_month = []
		cal = calendar.Calendar()
		month_days_weeks = cal.monthdatescalendar(year, month)
		for week_days in month_days_weeks:
			for day_date in week_days:
				if day_date.year == year and day_date.month == month:
					iso_year, iso_week, _ = day_date.isocalendar()
					if iso_year == year and iso_week not in weeks_in_month:
						weeks_in_month.append(iso_week)
					break
		return weeks_in_month


class UserHRZoneStatusView(APIView):
	"""Check if the authenticated user has any HR zones defined."""

	permission_classes = [IsAuthenticated]

	def get(self, request: Request) -> Response:
		user_profile = request.user.strava_profile
		if not user_profile:
			return Response(
				{"has_hr_zones": False, "error": "Strava profile not found for user."},
				status=status.HTTP_404_NOT_FOUND,
			)

		has_zones = HeartRateZone.objects.filter(config__user=user_profile).exists()
		return Response({"has_hr_zones": has_zones}, status=status.HTTP_200_OK)


class FetchStravaHRZonesView(APIView):
	"""View to trigger fetching and storing of Strava HR zones for the authenticated user."""

	permission_classes = [IsAuthenticated]

	def post(self, request: Request) -> Response:
		user = request.user
		try:
			user_strava_profile = user.strava_profile
		except StravaUser.DoesNotExist:
			return Response(
				{"error": "Strava account not linked or profile missing."},
				status=status.HTTP_404_NOT_FOUND,
			)

		try:
			worker = StravaHRWorker(user_strava_id=user_strava_profile.strava_id)
			success = worker.fetch_and_store_strava_hr_zones()

			if success:
				# The worker logs specifics. Here, just confirm the operation.
				return Response(
					{"message": "Strava HR zones fetch process completed."},
					status=status.HTTP_200_OK,
				)
			# This case implies a handled failure within the worker, e.g., API error.
			# The worker should have logged the specific reason.
			return Response(
				{"error": "Failed to fetch Strava HR zones. See server logs for details."},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR,
			)
		except Exception as e:
			logger.exception(
				f"Error fetching Strava HR zones for user {user_strava_profile.strava_id}: {e}"
			)
			return Response(
				{"error": "An unexpected error occurred while fetching HR zones."},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR,
			)


class UserHRZonesDisplayView(TemplateView):
	"""Displays the user's configured heart rate zones."""

	template_name = "api/hr_zone_display.html"

	permission_classes = [IsAuthenticated]

	def get_context_data(self, **kwargs: Any) -> dict:
		context = super().get_context_data(**kwargs)
		user = self.request.user

		user_configs_data = []
		error_message = None
		existing_activities = []

		if user.is_authenticated:
			try:
				strava_user = user.strava_profile

				# Fetch all configs for the user, with prefetch for zones
				all_user_configs_qs = CustomZonesConfig.objects.filter(
					user=strava_user
				).prefetch_related("zones_definition")

				def sort_key(config_obj):
					# Sorts DEFAULT config first, then by activity_type value, then created_at
					if config_obj.activity_type == ActivityType.DEFAULT:
						return (0, config_obj.created_at)
					# Fallback to string of activity_type if .value is not present
					activity_sort_val = getattr(
						config_obj.activity_type, "value", str(config_obj.activity_type)
					)
					return (1, activity_sort_val, config_obj.created_at)

				sorted_configs = sorted(all_user_configs_qs, key=sort_key)

				if sorted_configs:
					for config_item in sorted_configs:
						zones = sorted(config_item.zones_definition.all(), key=lambda z: z.min_hr)
						user_configs_data.append({"config": config_item, "zones": zones})
				else:
					error_message = "No custom HR zone configurations found. Please set one up."

				# Populate existing_activities
				#  (used for UI hints, e.g., which activities already have configs)
				existing_activities = CustomZonesConfig.objects.filter(
					user=strava_user
				).values_list("activity_type", flat=True)

			except StravaUser.DoesNotExist:
				error_message = (
					"Strava profile not found. Cannot fetch custom zone configurations."
				)
			except Exception as e:
				logger.exception(
					f"Error fetching/processing custom HR zones for user {user.id}: {e!r}"
				)
				error_message = "An unexpected error occurred while fetching your custom HR zones."
				# Consider clearing user_configs_data = [] here if partial data is problematic
		else:
			error_message = "User not authenticated."

		context["user_zone_configurations"] = user_configs_data
		context["error_message"] = error_message
		context["existing_activity_types_json"] = json.dumps(list(existing_activities))
		return context
