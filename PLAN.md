# Development Plan

This file outlines the development steps and serves as a checklist to track progress.

## Phase 1: Backend Foundation

- [x] **Initial Setup & Database Models**
    - [x] Install dependencies (`Django`, `djangorestframework`, `psycopg2-binary`, `requests`, `python-dotenv`, `cryptography`).
    - [x] Initialize Django project (`strava_zones_backend`).
    - [x] Initialize Django app (`api`).
    - [x] Define Django model: `User` (Strava ID, encrypted tokens, etc.).
    - [x] Define Django model: `CustomZoneSetting` (user FK, activity_type, boundaries JSON/TextField).
    - [x] Define Django model: `ZoneSummary` (user FK, year, month, week, zone_data JSON/TextField).
    - [x] Configure `settings.py` for `api` app, `rest_framework`, and database connection (using `.env`).
    - [x] Create initial database migrations (`makemigrations api`).
    - [x] Apply initial database migrations (`migrate`).
- [x] **Strava Authentication**
    - [x] Register application on Strava Developers (get Client ID/Secret).
    *   [x] Store Client ID/Secret securely (e.g., in `.env`).
    - [x] Implement `/api/auth/strava` view (redirects to Strava OAuth URL).
    - [x] Implement `/api/auth/strava/callback` view:
        - [x] Handle code exchange with Strava.
        - [x] Fetch basic user info from Strava.
        - [x] Encrypt tokens.
        - [x] Create or update `User` record in the database.
        - [x] Implement basic session/token management for *our* backend.
    - [x] Add URL patterns for auth views.
- [x] **Custom Zone Settings API**
    - [x] Implement `GET /api/zones/settings` view (retrieve settings for logged-in user).
    - [x] Implement `POST /api/zones/settings` view (create/update settings for logged-in user).
    - [x] Add URL patterns for settings views.
    - [x] Add serializers (DRF) for `CustomZoneSetting`.

## Phase 2: Backend Core Logic

- [ ] **Strava Data Fetching Utilities**
    - [x] Create utility/service function to get valid Strava access token (handle refresh if needed).
    - [x] Create utility/service function to fetch activities for a user/period (`GET /athlete/activities`).
    - [ ] Create utility/service function to fetch HR stream for an activity (`GET /activities/{id}/streams?keys=heartrate,time`).
- [ ] **HR Stream Processing**
    - [ ] Implement logic to parse HR stream data.
    - [ ] Implement logic to determine the custom zone for a given HR value based on user settings and activity type.
    - [ ] Implement logic to calculate total time spent in each custom zone for a single activity.
- [ ] **Data Aggregation & Storage**
    - [ ] Create a mechanism (e.g., Django management command `process_activities`) to:
        - [ ] Iterate through users or process for a specific user.
        *   [ ] Fetch activities for a given period.
        *   [ ] For each activity, fetch HR stream.
        *   [ ] Process stream against custom zones.
        *   [ ] Aggregate results weekly and monthly.
        *   [ ] Store aggregated results in `ZoneSummary` table (create or update).
    - [ ] Consider background task queue (like Celery) for long-running processing (optional initial step).

## Phase 3: Backend API Endpoint

- [ ] **Zone Data API Endpoint**
    - [ ] Implement `GET /api/zones?year=YYYY&month=MM` view.
    - [ ] Add authentication/permission checks (user must be logged in).
    - [ ] Retrieve relevant weekly/monthly `ZoneSummary` data from DB for the user/period.
    - [ ] (Optional) Trigger background processing if data is missing/stale.
    - [ ] Add URL pattern for the zones data view.
    - [ ] Add serializer (DRF) for `ZoneSummary`.

## Phase 4: Chrome Extension Frontend

- [ ] **Basic Structure & Authentication Trigger**
    - [ ] Create `extension` directory.
    - [ ] Create `manifest.json` (version 3):
        - [ ] Define name, version, description.
        - [ ] Request permissions (`storage`, `identity` (optional), host permissions for `strava.com` and backend URL).
        - [ ] Define background service worker.
        - [ ] Define content script (target `strava.com/athlete/calendar*`).
        - [ ] Define browser action (popup).
    - [ ] Create simple `popup.html` and `popup.js`.
    - [ ] Add button/link in popup to initiate auth (redirects to backend `/api/auth/strava`).
    - [ ] Create `background.js` to handle potential message passing.
    - [ ] Implement logic to store/manage backend session/token in `chrome.storage.local` after successful auth callback.
- [ ] **Custom Zone UI**
    - [ ] Create an options page (`options.html`, `options.js`) or enhance the popup UI.
    - [ ] Add form elements to define zone boundaries per activity type.
    - [ ] Implement JS in options/popup to:
        - [ ] Fetch current settings from `GET /api/zones/settings`.
        - [ ] Send updated settings via `POST /api/zones/settings`.
- [ ] **Calendar Injection & Data Display**
    - [ ] Create `content.js`.
    - [ ] Implement logic to detect calendar load/navigation.
    - [ ] Extract current year/month from the Strava page.
    *   [ ] Make authenticated call to backend `GET /api/zones` endpoint with year/month.
    - [ ] Parse the JSON response (weekly/monthly aggregated data).
    - [ ] Identify target locations in the Strava DOM to inject plots.
    - [ ] Generate HTML elements for bar plots based on received data.
    - [ ] Inject plots into the DOM.
    - [ ] Create `styles.css` for styling the plots and inject it.

## Phase 5: Refinement

- [x] Implement Strava token refresh logic in the backend.
- [ ] Add comprehensive error handling (API limits, network errors, backend errors, extension errors).
- [ ] Improve UI/UX of the extension popup, options page, and injected plots.
- [ ] Add backend tests (unit/integration, especially for processing logic).
- [ ] Add frontend tests (optional).
- [ ] Consider optimisations for data fetching and processing.
