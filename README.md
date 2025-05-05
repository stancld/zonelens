# Strava HR Zone Calendar Visualization

## Overview

This project aims to enhance the Strava web experience by displaying aggregated Heart Rate (HR) zone data directly on the user's Training Calendar page. It consists of a Chrome Extension that communicates with a custom backend server to fetch, process, and store activity data obtained via the Strava API.

The goal is to visualize the time spent in different HR zones on a weekly and monthly basis using bar plots overlaid on the Strava calendar interface.

## Features

*   **Strava Authentication:** Securely authenticates users via Strava's OAuth2 flow, managed by the backend.
*   **Custom HR Zones:** Allows users to define their own Heart Rate zone boundaries, potentially different for running and cycling activities.
*   **HR Data Fetching:** Retrieves activity details and heart rate *streams* from the Strava API for the authenticated user.
*   **Data Aggregation:** Processes fetched HR streams against the user's *custom* zone definitions to calculate total time spent in each zone, aggregated weekly and monthly.
*   **Database Storage:** Stores user authentication tokens (encrypted), custom zone definitions, and aggregated zone summaries in a PostgreSQL database.
*   **API for Extension:** Provides RESTful API endpoints for the Chrome extension to retrieve aggregated zone data and manage custom zone settings.
*   **Calendar Visualization:** Injects bar plots representing weekly/monthly HR zone distribution into the Strava calendar page via the Chrome Extension.

## Technology Stack

*   **Frontend:**
    *   Chrome Extension
    *   JavaScript (for content scripts, background scripts)
    *   HTML / CSS (for rendering plots within the Strava page)
*   **Backend:**
    *   Python
    *   Django (Web Framework)
    *   Django REST Framework (for building the API)
    *   Psycopg2 (PostgreSQL adapter)
    *   Requests (for Strava API calls)
    *   Cryptography (for token encryption)
*   **Database:**
    *   PostgreSQL
*   **External Services:**
    *   Strava API (v3)

## Architecture

The system comprises three main components:

1.  **Chrome Extension:**
    *   Runs in the user's browser.
    *   **Content Script:** Injected into `strava.com/athlete/calendar`. Detects the current view (month/year), communicates with the backend API to fetch zone data, and manipulates the DOM to display the visualizations.
    *   **Background Script:** Manages communication with the backend, potentially handles the initiation of the authentication flow, and manages the user's session/token with the backend API.
2.  **Django Backend Server:**
    *   Handles the core logic.
    *   Manages the Strava OAuth2 authentication flow (callback handling, token exchange, secure token storage).
    *   Interacts with the Strava API using stored user tokens to fetch activity details and **heart rate streams** (`type: heartrate`).
    *   Retrieves user's custom zone definitions from the database.
    *   Processes HR streams, applying custom zones to calculate time-in-zone.
    *   Performs data aggregation (weekly/monthly zone totals).
    *   Stores and retrieves data from the PostgreSQL database.
    *   Exposes REST API endpoints for the Chrome Extension.
3.  **PostgreSQL Database:**
    *   Stores user information, including encrypted Strava API tokens (`access_token`, `refresh_token`).
    *   Stores user-defined **custom HR zone settings** (per activity type).
    *   Stores aggregated weekly and monthly HR zone summaries per user (using a flexible format like JSON to accommodate varying zone counts).

## Architecture Alternatives Considered

### 1. Pure Client-Side (Extension-Only) Approach

An alternative architecture, similar to applications like Elevate for Strava, involves performing all data fetching, processing, and storage directly within the Chrome Extension itself.

*   **Mechanism:** The extension would handle Strava authentication (using `chrome.identity`), fetch activity/stream data from the Strava API, process it according to user settings (stored in `chrome.storage`), and store the raw or aggregated data locally (potentially using `IndexedDB` or even an embedded analytical engine like DuckDB via WebAssembly).
*   **Pros:**
    *   No backend server required (reduced cost and infrastructure).
    *   Enhanced data privacy (all data remains on the user's machine).
    *   Potential for powerful client-side analytics if using libraries like DuckDB WASM.
*   **Cons:**
    *   Increased extension complexity (managing WASM, `IndexedDB`, client-side processing logic).
    *   Constrained by browser resources (CPU, RAM) and storage limits.
    *   No capability for background processing when the browser is closed.
    *   Difficult to expand to other platforms (web app, mobile) or sync data across devices.
    *   Potentially slower initial processing compared to a dedicated backend.

### 2. Backend API Approach (Chosen)

The decision was made to proceed with a dedicated backend API.

*   **Justification:** While requiring server infrastructure, this approach offers greater flexibility, scalability, and robustness. It allows for:
    *   Offloading complex or heavy data processing from the user's browser.
    *   Potential for background data syncing and pre-processing.
    *   Centralized data storage, enabling easier future expansion to other platforms or features.
    *   Simpler extension logic, primarily focused on UI rendering and API communication.

## Data Flow

1.  User installs extension and initiates authentication via a link/button.
2.  Extension redirects to the backend's `/api/auth/strava` endpoint.
3.  Backend redirects user to Strava for authorization.
4.  User authorizes, Strava redirects back to the backend's `/api/auth/strava/callback` endpoint with an authorization code.
5.  Backend exchanges code for tokens, encrypts and stores them in the DB linked to the Strava user ID.
6.  Backend establishes a session/token for the extension.
7.  (One-time/Periodic) User defines their custom HR zones via the extension, which calls the backend API to save them.
8.  When the user visits the Strava calendar, the extension's content script detects the month/year.
9.  Content script makes an authenticated request to the backend's `/api/zones` endpoint.
10. Backend checks the DB for existing aggregated data for that user/period.
11. If data is missing/stale:
    *   Backend fetches activities for the period from Strava.
    *   For each relevant activity, backend fetches the **heart rate stream**.
    *   Backend retrieves the user's **custom zone settings** for the activity type.
    *   Backend **processes the stream**, calculates time in each custom zone, aggregates the data, and stores it in the DB.
12. Backend returns the aggregated zone data (weekly/monthly totals based on custom zones) to the extension.
13. Extension's content script uses the data to generate and display bar plots on the calendar page.

## Key Backend API Endpoints (Planned)

*   `GET /api/auth/strava`: Initiates the Strava OAuth flow.
*   `GET /api/auth/strava/callback`: Handles the callback from Strava after user authorization.
*   `GET /api/zones?year=<YYYY>&month=<MM>`: Returns aggregated weekly and monthly zone data (based on custom zones) for the authenticated user for the specified period. (Requires backend authentication).
*   `GET /api/zones/settings`: Retrieves the user's custom zone settings.
*   `POST /api/zones/settings`: Allows the user to define/update their custom zone settings.

## Setup (High-Level)

1.  **Backend:**
    *   Set up a Python virtual environment.
    *   Install Django, DRF, psycopg2, requests, python-dotenv, cryptography.
    *   Configure database settings (PostgreSQL connection).
    *   Define Django models for users, custom zone settings, and zone summaries.
    *   Run database migrations (`makemigrations`, `migrate`).
    *   Implement API views and URL routing.
    *   Set up Strava API application credentials (Client ID, Client Secret) and store them securely (e.g., `.env` file).
    *   Run the Django development server.
2.  **Chrome Extension:**
    *   Create `manifest.json` defining permissions (access to `strava.com`, backend URL, potentially `identity`, `storage`).
    *   Develop UI elements for users to **manage their custom HR zones**.
    *   Develop content script (`content.js`) for DOM manipulation and API calls.
    *   Develop background script (`background.js`) for managing backend communication/session.
    *   Create basic HTML/CSS for the visualizations if needed.
    *   Load the extension in Chrome in developer mode.
