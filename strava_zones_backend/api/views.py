from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.models import StravaUser
from api.utils import encrypt_data

if TYPE_CHECKING:
	from typing import Annotated, TypedDict

	from rest_framework.request import Request

	Secret = Annotated[str, "credential"]

	class TokenPayload(TypedDict):
		client_id: str
		client_secret: Secret
		code: str
		grant_type: str


User = get_user_model()  # Get the active user model

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def strava_authorize(request: HttpRequest) -> HttpResponseRedirect:  # noqa: ARG001
	"""Redirects the user to Strava's authorization page."""
	scopes = "read,activity:read_all,profile:read_all"
	# TODO: Ensure STRAVA_CLIENT_ID is loaded into settings
	client_id = settings.STRAVA_CLIENT_ID
	# TODO: Replace with actual callback URL, potentially from settings
	redirect_uri = "http://127.0.0.1:8000/api/auth/strava/callback"

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

		# Temporarily store athlete data if needed, or just proceed
		# For now, focus on getting the token and user association right
		athlete_info = token_data.get("athlete", {})
		strava_id = athlete_info.get("id")

		if not strava_id:
			logging.error("Strava ID not found in token response.")
			return HttpResponseBadRequest("Could not retrieve Strava user ID.")

		user = _get_user(
			username=f"strava_{strava_id}",
			strava_id=strava_id,
			athlete_info=athlete_info,
			token_data=token_data,
			request=request,
		)

		# Create or get the DRF token for API authentication
		drf_token, _ = Token.objects.get_or_create(user=user)

		# Render the template to pass the token back to the frontend
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


def _generate_token_payload(code: str) -> TokenPayload:
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
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def user_profile(request: Request) -> Response:
	"""Returns basic profile info for the authenticated user."""
	user = request.user
	return Response(
		{
			"username": user.username,
			"first_name": user.first_name,
			"last_name": user.last_name,
			"strava_id": user.strava_profile.strava_id
			if hasattr(user, "strava_profile")
			else None,
		}
	)
