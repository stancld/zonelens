# Strava HR Zone Calendar Visualization

<div align="center">

[![Python version](https://img.shields.io/badge/python-3.12-blue)](https://img.shields.io/badge/python-3.12-blue)
[![codecov](https://codecov.io/github/stancld/strava-zones/graph/badge.svg?token=LNOJHFDBUA)](https://codecov.io/github/stancld/strava-zones)

______________________________________________________________________

</div>

## ✨ Project Goal

This project enhances the Strava web experience by displaying aggregated Heart Rate (HR) zone data directly on the user's Training Calendar. It involves a Chrome Extension that communicates with a Django backend to fetch, process, and visualize activity data from the Strava API.

The main aim is to provide a visual representation of time spent in different HR zones on a weekly and monthly basis.

## 🧪 Experimental & Learning Focus

This is an **experimental project** primarily developed as an exercise in **"vibe-coding"** – rapidly iterating and building features with a focus on learning and exploration rather than achieving maximum efficiency or a production-ready state. As such, some design choices or implementation details might reflect this learning process.

## 📸 Current State (as of May 16, 2025)

![Current WIP Calendar Screenshot](images/wip_calendar_250516.png)

![Current WIP HR config Screenshot](images/wip_hr_config_250523.png)

## Core Features

*   **Strava Authentication:** Secure OAuth2 flow managed by the backend.
*   **Custom HR Zones:** (Planned) Users will be able to define their own HR zone boundaries.
*   **Data Processing:** Fetches HR streams, aggregates time-in-zone against custom definitions (monthly & weekly).
*   **Calendar Visualization:** Injects HR zone summaries into the Strava calendar page.

## 🛠️ Technology Stack

*   **Frontend:** Chrome Extension (JavaScript, HTML, CSS)
*   **Backend:** Python, Django, Django REST Framework
*   **Database:** PostgreSQL
*   **External:** Strava API (v3)

## ⚙️ Basic Architecture

A client-server model:
1.  **Chrome Extension:** Runs in the browser, detects calendar view, calls the backend for data, and renders visualizations on the Strava page.
2.  **Django Backend:** Handles Strava authentication, fetches and processes activity/HR data from the Strava API, stores aggregated summaries, and serves data to the extension via a REST API.

## 🚀 Quick Setup Guide

1.  **Backend (Django):**
    *   Set up Python & PostgreSQL.
    *   Install dependencies from `requirements.txt` (if available, or see `PLAN.md` for a list).
    *   Configure `.env` with Strava API credentials and database settings.
    *   Run database migrations.
    *   Start the Django development server.
2.  **Chrome Extension:**
    *   Navigate to `chrome://extensions`.
    *   Enable "Developer mode".
    *   Click "Load unpacked" and select the extension's frontend directory.

## Key API Endpoints

*   **Authentication & Profile:**
    *   `/api/auth/strava/`: Initiates Strava OAuth2 authentication.
    *   `/api/auth/strava/callback/`: Handles Strava's OAuth2 callback.
    *   `/api/profile/`: Displays the user's profile information (requires authentication).
*   **Data Synchronization:**
    *   `/api/strava/sync-activities/`: `POST` Triggers a sync of recent activities from Strava (requires authentication).
*   **Heart Rate Zone Management & Display:**
    *   `/api/settings/custom-zones/`:
        *   `GET`: Lists all custom HR zone configurations for the authenticated user.
        *   `POST`: Creates a new custom HR zone configuration for the authenticated user.
    *   `/api/settings/custom-zones/<uuid:pk>/` (Requires authentication):
        *   `GET`: Retrieves a specific custom HR zone configuration by its UUID.
        *   `PUT`: Updates a specific custom HR zone configuration.
        *   `DELETE`: Deletes a specific custom HR zone configuration.
    *   `/api/fetch-strava-hr-zones/`: `POST` Fetches HR zones directly from Strava and updates/creates the user's 'DEFAULT' configuration (requires authentication).
    *   `/api/user/hr-zones/`: `GET` Renders the HTML page for viewing and editing custom HR zones (`hr_zone_display.html`) (requires authentication).
    *   `/api/user/hr-zone-status/`: `GET` Provides the status of the user's HR zone configuration (e.g., if default Strava zones are used or if custom zones are set) (requires authentication).
*   **Aggregated Data for Extension:**
    *   `/api/zones/`: `GET` Provides aggregated zone data (e.g., time in zones per week/month). Accepts query parameters like `year`, `month`, `week`, `period_type` (requires authentication).

(For more detailed setup and historical planning, see `PLAN.md`.)
