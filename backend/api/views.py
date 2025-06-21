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

import contextlib
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import (
	HttpRequest,
	HttpResponse,
	HttpResponseBadRequest,
	HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View
from rest_framework import generics, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import (
	ActivityProcessingQueue,
	ActivityType,
	CustomZonesConfig,
	HeartRateZone,
	StravaUser,
	ZoneSummary,
	get_default_processing_start_time,
)
from api.serializers import CustomZonesConfigSerializer, ZoneSummarySerializer
from api.strava_client import StravaApiClient
from api.utils import determine_weeks_in_month, encrypt_data
from api.worker import Worker

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
	callback_path = reverse("strava_callback")
	redirect_uri = request.build_absolute_uri(callback_path)

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

		context = {
			"token": drf_token.key,
			"frontend_redirect_url": reverse("user_hr_zones_display"),
		}
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

	strava_user, strava_user_created = StravaUser.objects.update_or_create(
		strava_id=strava_id,
		defaults={
			"user": user,
			"access_token": encrypt_data(token_data["access_token"]),
			"refresh_token": encrypt_data(token_data["refresh_token"]),
			"token_expires_at": timezone.make_aware(
				datetime.fromtimestamp(token_data["expires_at"])
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
	context = {}
	if request.user.is_authenticated:
		try:
			queue_entry = ActivityProcessingQueue.objects.get(user__user=request.user)
			# Only show status if total_activities is known (not None)
			if queue_entry.total_activities is not None:
				context["num_processed"] = queue_entry.num_processed
				context["total_activities"] = queue_entry.total_activities
		except ActivityProcessingQueue.DoesNotExist:
			pass  # No queue entry, nothing to add to context
		except Exception as e:
			logger.error(f"Error fetching ActivityProcessingQueue for user {request.user.id}: {e}")
	return render(request, "index.html", context)


class ProfileView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request: Request) -> Response:
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

	def delete(self, request: Request) -> Response:
		"""Delete the user's account and associated data."""
		try:
			user = request.user
			# The on_delete=models.CASCADE on the StravaUser.user field will handle
			# deleting the associated StravaUser, HR data, config etc.
			user.delete()
			return Response(
				{"message": "Account deleted successfully."},
				status=status.HTTP_204_NO_CONTENT,
			)
		except Exception as e:
			logger.error(f"Error deleting account for user {request.user.id}: {e}")
			return Response(
				{"error": "An error occurred during account deletion."},
				status=status.HTTP_500_INTERNAL_SERVER_ERROR,
			)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sync_status(request: Request) -> Response:
	"""Returns activity sync status for the authenticated user."""
	user = request.user
	data = {}

	with contextlib.suppress(ActivityProcessingQueue.DoesNotExist):
		strava_user = user.strava_profile
		queue_entry = ActivityProcessingQueue.objects.get(user=strava_user)

		if queue_entry.total_activities is not None:
			data["sync_status"] = {
				"num_processed": queue_entry.num_processed,
				"total_activities": queue_entry.total_activities,
			}

	return Response(data)


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
			strava_user = StravaUser.objects.get(user=request.user)
		except StravaUser.DoesNotExist:
			return Response(
				{"error": "Strava profile not found for the current user."},
				status=status.HTTP_404_NOT_FOUND,
			)

		# Add user to the queue if they aren't already there.
		_obj, created = ActivityProcessingQueue.objects.get_or_create(user=strava_user)
		if created:
			logger.info(f"User {strava_user.strava_id} added to the activity processing queue.")
			message = "Activity processing has been initiated and will run in the background."
		else:
			message = (
				"Activity processing is already in progress and will continue in the background."
			)

		return Response({"status": message}, status=status.HTTP_202_ACCEPTED)


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
		for week in sorted(determine_weeks_in_month(year, month)):
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

		zone_definitions_map = {}
		try:
			# Get the DEFAULT custom zones configuration for the user - Names are shared
			default_config = CustomZonesConfig.objects.prefetch_related("zones_definition").get(
				user=user_profile, activity_type=ActivityType.DEFAULT
			)
			for zone_model_instance in default_config.zones_definition.all():
				zone_key = f"zone{zone_model_instance.order}"
				zone_definitions_map[zone_key] = zone_model_instance.name
		except CustomZonesConfig.DoesNotExist:
			logger.warning(
				f"No DEFAULT CustomZonesConfig found for user {user_profile.strava_id}. "
				f"Frontend will use fallback zone names."
			)

		return Response(
			{
				"message": "Zone summary data retrieved successfully.",
				"year": year,
				"month": month,
				"monthly_summary": monthly_serializer.data,
				"weekly_summaries": weekly_serializer.data,
				"zone_definitions": zone_definitions_map,
			},
			status=status.HTTP_200_OK,
		)


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
			worker = Worker(user_strava_id=user_strava_profile.strava_id)
			if _success := worker.fetch_and_store_strava_hr_zones():
				# Check if user has HR zones defined now and add to queue if it's the first time.
				if CustomZonesConfig.objects.filter(user=user_strava_profile).exists():
					_obj, created = ActivityProcessingQueue.objects.get_or_create(
						user=user_strava_profile
					)
					if created:
						logger.info(
							f"User {user_strava_profile.strava_id} added to activity processing "
							"queue after fetching Strava HR zones."
						)
					try:
						strava_api_client = StravaApiClient(user_strava_profile)
						start_time_dt = get_default_processing_start_time()
						start_timestamp = int(start_time_dt.timestamp())
						activities = strava_api_client.fetch_all_strava_activities(
							after=start_timestamp
						)
						if activities is not None:
							_obj.total_activities = len(activities)
							_obj.save(update_fields=["total_activities"])
						else:
							logger.warning(
								f"Could not fetch activities to determine total for user "
								f"{user_strava_profile.strava_id}. total_activities will be None."
							)
							_obj.total_activities = None  # Explicitly set to None if fetch fails
							_obj.save(update_fields=["total_activities"])
					except Exception as e:
						logger.error(
							f"Failed to count activities for user {user_strava_profile.strava_id}: {e}"  # noqa: E501
						)
						_obj.total_activities = None  # Ensure it's None on error
						_obj.save(update_fields=["total_activities"])

				successful_message = "Strava HR zones fetch process completed."
				if created:
					successful_message += (
						" Strava activities will be processed as of January 1, 2025."
					)
				return Response({"message": successful_message}, status=status.HTTP_200_OK)
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


class UserHRZonesDisplayView(LoginRequiredMixin, TemplateView):
	"""Displays the user's configured heart rate zones and handles updates."""

	template_name = "api/hr_zone_display.html"

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
					error_message = (
						"No custom HR zone configurations found. "
						"Please fetch default from Strava to initial setup."
					)

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
		# Pass ActivityType enum to context for the "Add Activity Config" modal/form
		context["activity_types_enum"] = [
			{"value": choice.value, "label": choice.label}
			for choice in ActivityType  # type: ignore[attr-defined]
			if choice != ActivityType.DEFAULT
		]

		# Add activity processing queue status
		if user.is_authenticated and hasattr(user, "strava_profile"):
			try:
				queue_entry = ActivityProcessingQueue.objects.get(user=user.strava_profile)
				if queue_entry.total_activities is not None:
					context["num_processed"] = queue_entry.num_processed
					context["total_activities"] = queue_entry.total_activities
			except ActivityProcessingQueue.DoesNotExist:
				pass  # No queue entry, nothing to add
			except Exception as e:
				logger.error(f"Error fetching ActivityProcessingQueue for user {user.id}: {e}")

		return context

	def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
		action = request.POST.get("action")
		user = request.user

		if not user.is_authenticated:
			messages.error(request, "User not authenticated.")
			return redirect(request.path_info)

		try:
			strava_user = user.strava_profile
		except StravaUser.DoesNotExist:
			messages.error(request, "Strava profile not found.")
			return redirect(request.path_info)

		if action == "save_all_zone_configs":
			return self._handle_save_all_zone_configs(request, strava_user)
		if action == "delete_activity_config":
			return self._handle_delete_activity_config(request, strava_user)
		if action and action.startswith("add_default_zones_to_"):
			config_id_to_add_defaults = action.split("add_default_zones_to_")[-1]
			return self._handle_add_default_zones(request, strava_user, config_id_to_add_defaults)
		if action == "add_new_activity_config":
			return self._handle_add_new_activity_config(request, strava_user)

		messages.error(request, "Invalid action specified.")
		return redirect(request.path_info)

	@transaction.atomic
	def _handle_save_all_zone_configs(  # noqa: C901, PLR0915
		self, request: HttpRequest, strava_user: StravaUser
	) -> HttpResponseRedirect:
		form_data = request.POST
		parsed_configs: dict = {}

		# Parse form data into a structured dictionary
		# Expected keys: configs[idx][id], configs[idx][activity_type],
		# configs[idx][zones][z_idx][id], ...[name], ...[min_hr], ...[max_hr], ...[order]
		for key, value in form_data.items():
			if key.startswith("configs["):
				parts = key.replace("]", "").split("[")
				try:
					config_idx = int(parts[1])
					parsed_configs.setdefault(config_idx, {"zones": {}})

					if len(parts) == 3:  # Config level attribute (id, activity_type)
						attr_name = parts[2]
						if attr_name in ["id", "activity_type"]:
							parsed_configs[config_idx][attr_name] = value
					elif len(parts) == 5 and parts[2] == "zones":  # Zone level attribute
						zone_idx = int(parts[3])
						zone_attr = parts[4]
						parsed_configs[config_idx]["zones"].setdefault(zone_idx, {})
						parsed_configs[config_idx]["zones"][zone_idx][zone_attr] = (
							value.strip() if isinstance(value, str) else value
						)
				except (IndexError, ValueError) as e:
					logger.warning(
						f"Could not parse form key: {key} with value: {value}. Error: {e}"
					)
					continue  # Skip malformed keys

		if not parsed_configs:
			messages.error(request, "No configuration data received or data was malformed.")
			return redirect(request.path_info)

		default_config_zone_names_by_order = {}

		# First pass: Validate configs and get default config's zone names
		for config_idx in sorted(parsed_configs.keys()):
			data = parsed_configs[config_idx]
			config_id = data.get("id")
			activity_type_val = data.get("activity_type")

			if not config_id or not activity_type_val:
				messages.error(
					request,
					f"Missing ID or Activity Type for a configuration. Index: {config_idx}",
				)
				return redirect(request.path_info)  # Abort transaction

			try:
				# Validate config ownership early
				CustomZonesConfig.objects.get(
					id=config_id, user=strava_user, activity_type=activity_type_val
				)
			except CustomZonesConfig.DoesNotExist:
				messages.error(
					request,
					"Invalid or unauthorized configuration: "
					f"ID {config_id}, Type {activity_type_val}.",
				)
				return redirect(request.path_info)  # Abort transaction

			if activity_type_val == ActivityType.DEFAULT:
				for zone_idx in sorted(data["zones"].keys()):
					zone_data = data["zones"][zone_idx]
					try:
						order = int(zone_data.get("order"))
						name = zone_data.get("name", f"Zone {order}").strip()
						if not name:
							name = f"Zone {order}"
						default_config_zone_names_by_order[order] = name
					except (ValueError, TypeError):
						messages.error(
							request,
							f"Invalid zone order or name for Default config. Zone {zone_idx}",
						)
						return redirect(request.path_info)  # Abort transaction

		# Second pass: Update/create zones for all configs
		for config_idx in sorted(parsed_configs.keys()):
			data = parsed_configs[config_idx]
			config_instance = CustomZonesConfig.objects.get(
				id=data["id"], user=strava_user
			)  # Already validated

			form_zone_ids_for_this_config = set()
			zones_data_for_config = sorted(
				data["zones"].items(), key=lambda x: int(x[1].get("order", 0))
			)

			for _zone_idx_key, zone_data in zones_data_for_config:
				zone_id_str = zone_data.get("id")
				if zone_id_str and zone_id_str.strip():
					try:
						form_zone_ids_for_this_config.add(int(zone_id_str))
					except ValueError:
						logger.warning(
							f"Invalid zone ID '{zone_id_str}' for config {config_instance.id}"
						)

			# Delete zones associated with this config that are not in the current submission
			HeartRateZone.objects.filter(config=config_instance).exclude(
				id__in=form_zone_ids_for_this_config
			).delete()

			for _zone_idx_key, zone_data in zones_data_for_config:
				zone_id = zone_data.get("id")
				try:
					order = int(zone_data.get("order"))
					min_hr_str = zone_data.get("min_hr", "0")
					max_hr_str = zone_data.get("max_hr")

					min_hr = int(min_hr_str) if min_hr_str and min_hr_str.strip() else 0

					if (
						max_hr_str is None
						or max_hr_str.strip().lower() == "open"
						or max_hr_str.strip() == ""
					):
						max_hr = 220
					else:
						max_hr = int(max_hr_str)

					min_hr = max(min_hr, 0)
					if max_hr <= min_hr and max_hr != 220:
						messages.warning(
							request,
							f"Max HR ({max_hr}) must be greater than Min HR ({min_hr}) for zone "
							f"{order} in {config_instance.get_activity_type_display()}. "
							"Skipping update for this zone.",
						)
						continue
				except (ValueError, TypeError) as e:
					messages.error(
						request,
						f"Invalid HR value for zone {zone_data.get('name', 'with order ' + str(order))} in "  # noqa: E501
						f"{config_instance.get_activity_type_display()}: {e}",
					)
					continue

				zone_name_from_form = zone_data.get("name", f"Zone {order}").strip()
				if not zone_name_from_form:
					zone_name_from_form = f"Zone {order}"

				final_zone_name = zone_name_from_form
				if config_instance.activity_type != ActivityType.DEFAULT:
					final_zone_name = default_config_zone_names_by_order.get(
						order, zone_name_from_form
					)

				zone_defaults = {
					"name": final_zone_name,
					"min_hr": min_hr,
					"max_hr": max_hr,
					"order": order,
				}

				if zone_id and zone_id.strip():
					try:
						# Ensure the zone_id is an integer if it's not empty
						HeartRateZone.objects.update_or_create(
							id=int(zone_id),
							config=config_instance,  # Ensure it belongs to the current config
							defaults=zone_defaults,
						)
					except ValueError:
						# zone_id was not a valid int, treat as new if other fields are valid
						logger.warning(
							f"Invalid zone ID '{zone_id}' for update. "
							"Attempting to create as new zone if data is valid."
						)
						HeartRateZone.objects.create(config=config_instance, **zone_defaults)
					except (
						HeartRateZone.DoesNotExist
					):  # Should be caught by update_or_create, but good practice
						HeartRateZone.objects.create(config=config_instance, **zone_defaults)
				else:
					HeartRateZone.objects.create(config=config_instance, **zone_defaults)

		messages.success(request, "Heart rate zones saved successfully!")
		return redirect(request.path_info)

	@transaction.atomic
	def _handle_add_default_zones(self, request, strava_user, config_id):
		try:
			config_to_update = CustomZonesConfig.objects.get(id=config_id, user=strava_user)
			if config_to_update.zones_definition.exists():
				messages.warning(request, "This configuration already has zones defined.")
			else:
				try:
					default_config = CustomZonesConfig.objects.get(
						user=strava_user, activity_type=ActivityType.DEFAULT
					)
					default_zones = default_config.zones_definition.all().order_by("order")
					if not default_zones.exists():
						messages.error(
							request, "Default zone configuration is empty or not found."
						)
						return redirect(request.path_info)

					for zone in default_zones:
						HeartRateZone.objects.create(
							config=config_to_update,
							name=zone.name,
							min_hr=zone.min_hr,
							max_hr=zone.max_hr,
							order=zone.order,
						)
				except CustomZonesConfig.DoesNotExist:
					messages.error(request, "Default zone configuration not found.")
					return redirect(request.path_info)
				messages.success(
					request,
					f"Default zones added to {config_to_update.get_activity_type_display()}.",
				)
		except CustomZonesConfig.DoesNotExist:
			messages.error(request, "Configuration not found.")
		except Exception as e:
			logger.error(f"Error adding default zones for config {config_id}: {e!r}")
			messages.error(request, "An error occurred while adding default zones.")
		return redirect(request.path_info)

	@transaction.atomic
	def _handle_add_new_activity_config(
		self, request: HttpRequest, strava_user: StravaUser
	) -> HttpResponse:
		activity_type_value = request.POST.get("new_activity_type")
		if not activity_type_value:
			messages.error(request, "No activity type selected.")
			return redirect(request.path_info)

		is_valid_activity = any(
			choice.value == activity_type_value
			for choice in ActivityType  # type: ignore[attr-defined]
		)
		if not is_valid_activity or activity_type_value == ActivityType.DEFAULT.value:  # type: ignore[attr-defined]
			messages.error(
				request, "Invalid activity type selected or DEFAULT cannot be added again."
			)
			return redirect(request.path_info)

		if CustomZonesConfig.objects.filter(
			user=strava_user, activity_type=activity_type_value
		).exists():
			messages.warning(
				request,
				f"A configuration for {ActivityType(activity_type_value).label} already exists.",
			)
			return redirect(request.path_info)

		try:
			new_config = CustomZonesConfig.objects.create(
				user=strava_user, activity_type=activity_type_value
			)
			default_config = (
				CustomZonesConfig.objects.filter(
					user=strava_user, activity_type=ActivityType.DEFAULT
				)
				.prefetch_related("zones_definition")
				.first()
			)

			if default_config:
				default_zones = default_config.zones_definition.all().order_by("order")
				for dz in default_zones:
					HeartRateZone.objects.create(
						config=new_config,
						name=dz.name,
						min_hr=dz.min_hr,
						max_hr=dz.max_hr,
						order=dz.order,
					)
			messages.success(
				request, f"Configuration for {ActivityType(activity_type_value).label} added."
			)

			# Check if user has any other HR zone configs. If not, this is their first.
			# Add to processing queue only if it's the first config.
			if CustomZonesConfig.objects.filter(user=strava_user).count() == 1:
				_obj, created = ActivityProcessingQueue.objects.get_or_create(user=strava_user)
				if created:
					logger.info(
						f"User {strava_user.strava_id} added to activity processing queue."
					)
				try:
					strava_api_client = StravaApiClient(strava_user)
					start_time_dt = get_default_processing_start_time()
					start_timestamp = int(start_time_dt.timestamp())
					activities = strava_api_client.fetch_all_strava_activities(
						after=start_timestamp
					)
					if activities is not None:
						_obj.total_activities = len(activities)
						_obj.save(update_fields=["total_activities"])
						logger.info(
							f"Found {len(activities)} activities to sync for user "
							f"{strava_user.strava_id} since {start_time_dt.date()}."
						)
					else:
						logger.warning(
							f"Could not fetch activities to determine total for user "
							f"{strava_user.strava_id}. total_activities will be None."
						)
						_obj.total_activities = None  # Explicitly set to None if fetch fails
						_obj.save(update_fields=["total_activities"])
				except Exception as e_count:
					logger.error(
						f"Error counting activities for user {strava_user.strava_id}: {e_count}"
					)
					_obj.total_activities = None  # Ensure it's None on error
					_obj.save(update_fields=["total_activities"])

		except Exception as e:
			logger.error(f"Error creating new activity config for {activity_type_value}: {e!r}")
			messages.error(request, "Failed to add new configuration.")

		return redirect(request.path_info)

	@transaction.atomic
	def _handle_delete_activity_config(
		self, request: HttpRequest, strava_user: StravaUser
	) -> HttpResponseRedirect:
		config_id_to_delete = request.POST.get("config_id_to_delete")

		if not config_id_to_delete:
			messages.error(request, "Configuration ID not provided for deletion.")
			return redirect(request.path_info)

		try:
			# Ensure the config belongs to the user and exists
			config_to_delete = get_object_or_404(
				CustomZonesConfig, id=config_id_to_delete, user=strava_user
			)

			# Prevent deletion of the DEFAULT configuration
			if config_to_delete.activity_type == ActivityType.DEFAULT:
				messages.error(request, "The 'Default' configuration cannot be deleted.")
				return redirect(request.path_info)

			activity_type_display = (
				config_to_delete.get_activity_type_display()
			)  # Get display name before deleting
			config_to_delete.delete()
			messages.success(
				request, f"Successfully deleted the '{activity_type_display}' configuration."
			)

		except CustomZonesConfig.DoesNotExist:  # Should be caught by get_object_or_404
			messages.error(
				request, "Configuration not found or you do not have permission to delete it."
			)
		except Exception as e:
			logger.exception(
				f"Error deleting configuration {config_id_to_delete} for user {strava_user.id}: "
				f"{e!s}"
			)
			messages.error(
				request, "An unexpected error occurred while trying to delete the configuration."
			)

		return redirect(request.path_info)


class StravaWebhookAPIView(APIView):
	"""Handle Strava webhook events."""

	permission_classes: list = []  # Webhooks are not authenticated via DRF tokens

	def get(self, request: Request) -> Response:
		"""Verify webhook subscription."""
		hub_challenge = request.query_params.get("hub.challenge")
		hub_mode = request.query_params.get("hub.mode")
		hub_verify_token = request.query_params.get("hub.verify_token")

		hub_info = f"mode={hub_mode!r}, token={hub_verify_token!r}, challenge={hub_challenge!r}"
		logger.info(f"Strava webhook verification attempt: {hub_info}.")

		if hub_mode == "subscribe" and hub_verify_token == settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
			logger.info(
				f"Webhook verification successful. Responding with challenge: {hub_challenge}"
			)
			return Response({"hub.challenge": hub_challenge}, status=status.HTTP_200_OK)

		logger.error(f"Webhook verification failed. Mode: {hub_mode}.")
		return Response(
			{"status": "error", "message": "Verification failed"},
			status=status.HTTP_403_FORBIDDEN,
		)

	def post(self, request: Request) -> Response:
		"""Receive and process event notification."""
		event_data = request.data
		logger.info(f"Received Strava webhook event: {json.dumps(event_data)}")

		object_type = event_data.get("object_type")
		aspect_type = event_data.get("aspect_type")
		owner_id = event_data.get("owner_id")
		activity_id = event_data.get("object_id")

		if object_type == "activity" and aspect_type == "create":
			if response := self._check_for_owner_and_activity_ids(
				owner_id, activity_id, event_data
			):
				return response
			logger.info(f"Processing 'create activity' event activity_id: {activity_id}.")
			try:
				worker = Worker(user_strava_id=owner_id)
				worker.process_new_activity(user_strava_id=owner_id, activity_id=activity_id)
			except Exception as e:
				logger.exception(
					f"Unexpected error processing webhook for activity {activity_id}: {e}."
				)
		elif object_type == "activity" and aspect_type == "delete":
			if response := self._check_for_owner_and_activity_ids(
				owner_id, activity_id, event_data
			):
				return response
			logger.info(f"Processing 'delete activity' event activity_id: {activity_id}.")
			try:
				worker = Worker(user_strava_id=owner_id)
				worker.delete_activity(user_strava_id=owner_id, activity_id=activity_id)
			except Exception as e:
				logger.exception(
					f"Unexpected error processing webhook for deleting activity {activity_id}: {e}."  # noqa: E501
				)
		else:
			logger.info(
				f"Ignoring webhook event, object_type={object_type!r}, aspect_type={aspect_type!r}"
			)

		return Response({"status": "event received"}, status=status.HTTP_200_OK)

	@staticmethod
	def _check_for_owner_and_activity_ids(
		owner_id: int, activity_id: int, event_data: dict[str, Any]
	) -> Response | None:
		if not owner_id or not activity_id:
			logger.error(
				f"Missing owner_id ({owner_id}) or object_id ({activity_id}) "
				f"in webhook event: {event_data}"
			)
			return Response(
				{"status": "error", "message": "Missing owner_id or object_id"},
				status=status.HTTP_400_BAD_REQUEST,
			)
		return None


# Strava OAuth Views remain unchanged below
class StravaAuthorizeView(LoginRequiredMixin, View):
	"""Redirects the user to Strava's authorization page."""

	def get(self, request: HttpRequest) -> HttpResponseRedirect:
		scopes = "read,activity:read_all,profile:read_all"
		client_id = settings.STRAVA_CLIENT_ID
		callback_path = reverse("strava_callback")
		redirect_uri = request.build_absolute_uri(callback_path)

		params = {
			"client_id": client_id,
			"redirect_uri": redirect_uri,
			"response_type": "code",
			"approval_prompt": "auto",
			"scope": scopes,
		}

		return HttpResponseRedirect(f"{STRAVA_AUTH_URL}?{urlencode(params)}")
