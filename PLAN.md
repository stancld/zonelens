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

- [x] **Strava Data Fetching Utilities**
    - [x] Create utility/service function to get valid Strava access token (handle refresh if needed).
    - [x] Create utility/service function to fetch activities for a user/period (`GET /athlete/activities`).
    - [x] Create utility/service function to fetch HR stream for an activity (`GET /activities/{id}/streams?keys=heartrate,time`).
- [x] **HR Stream Processing**
    - [x] Implement logic to parse HR stream data.
    - [x] Implement logic to determine the custom zone for a given HR value based on user settings and activity type.
    - [x] Implement logic to calculate total time spent in each custom zone for a single activity.
- [x] **Data Aggregation & Storage**
    - [x] Create a mechanism (e.g., HTTP request to `process_activities`) to:
        - [x] Fetch activities for a given period.
        - [x] For each activity, fetch HR stream.
        - [x] Process stream against custom zones.
        - [x] Aggregate results weekly and monthly.
        - [x] Store aggregated results in `ZoneSummary` table (create or update).

## Phase 3: Backend API Endpoint

- [x] **Zone Data API Endpoint**
    - [x] Implement `GET /api/zones?year=YYYY&month=MM` view.
    - [x] Add authentication/permission checks (user must be logged in).
    - [x] Retrieve relevant weekly/monthly `ZoneSummary` data from DB for the user/period.
    - [x] Add URL pattern for the zones data view.
    - [x] Add serializer (DRF) for `ZoneSummary`.

## Phase 4: Chrome Extension Frontend

- [x] **Basic Structure & Authentication Trigger**
    - [x] Create `extension` directory.
    - [x] Create `manifest.json` (version 3):
        - [x] Define name, version, description.
        - [x] Request permissions (`storage`, `cookies`, host permissions for `strava.com` and backend URL).
        - [ ] Define background service worker.
        - [x] Define content script (target `strava.com/athlete/calendar*`).
        - [x] Define browser action (popup) and icons.
    - [x] Create simple `popup.html` and `popup.js`.
    - [x] Implement CSRF token handling for backend requests from extension (`settings.py`, `popup.js`).
    - [x] Configure local HTTPS development environment (`mkcert`, Django dev server settings).
    - [x] Add button/link in popup to initiate auth (redirects to backend `/api/auth/strava`).
    - [ ] Create `background.js` to handle potential message passing.
    - [ ] Implement logic to store/manage backend session/token in `chrome.storage.local` after successful auth callback.
- [x] **Custom Zone UI**
    - [x] Enable configuring HR zones boundaries.
    - [x] Add form elements to define zone boundaries per activity type.
    - [x] Implement JS in options/popup to:
        - [x] Fetch current settings from `GET /api/zones/settings`.
- [x] **Calendar Injection & Data Display**
    - [x] Create `content.js`.
    - [x] Implement logic to detect calendar load/navigation.
    - [x] Extract current year/month from the Strava page.
    - [x] Make authenticated call to backend `GET /api/zones` endpoint with year/month.
    - [x] Parse the JSON response (weekly/monthly aggregated data).
    - [x] Identify target locations in the Strava DOM to inject plots.
    - [x] Generate HTML elements for bar plots based on received data.
    - [x] Inject plots into the DOM.
    - [x] Create `styles.css` for styling the plots and inject it.

## Phase 5: Refinement

- [x] Implement Strava token refresh logic in the backend.
- [ ] Add comprehensive error handling (API limits, network errors, backend errors, extension errors).
- [x] Improve UI/UX of the extension popup (styling to match Strava, dynamic status messages).
- [ ] Improve UI/UX of the options page and injected plots.
- [x] Use environment variable for `CHROME_EXTENSION_ID` in `CSRF_TRUSTED_ORIGINS` (`settings.py`).
- [ ] Consider optimisations for data fetching and processing.

## Phase 6: Production-Level Deployment (AWS Free Tier)

- [ ] **Infrastructure Setup (AWS Free Tier)**
  - [x] Define infrastructure requirements (e.g., EC2 for backend, S3 for frontend static assets, RDS for database if needed, or SQLite on EC2).
  - [x] Set up IAM roles and permissions.
  - [x] Configure VPC, subnets, security groups.
  - [ ] Choose an appropriate EC2 instance type (t2.micro or t3.micro typically in free tier).
  - [ ] Set up RDS if chosen, or configure SQLite on the EC2 instance.
  - [ ] Configure S3 bucket for static frontend hosting (if applicable).
  - [ ] Set up CloudFront CDN for frontend (optional, but good practice).

- [ ] **Backend Deployment (Django)**
  - [x] Containerize the Django application (Docker).
  - [ ] Set up a process manager (e.g., Gunicorn, Supervisor) on EC2.
  - [ ] Configure a web server (e.g., Nginx) as a reverse proxy on EC2.
  - [ ] Manage static files (collectstatic) and media files.
  - [ ] Configure environment variables securely (e.g., AWS Systems Manager Parameter Store, or .env file with restricted permissions).
  - [ ] Set up logging and monitoring (e.g., CloudWatch Logs).
  - [ ] Database migrations.

- [ ] **Frontend Deployment (Static)**
  - [ ] Build frontend assets.
  - [ ] Deploy to S3 (if S3 hosting chosen).
  - [ ] Configure CloudFront to serve from S3 (if chosen).

- [ ] **Domain and HTTPS**
  - [ ] Register or use an existing domain name.
  - [ ] Configure DNS records (e.g., Route 53).
  - [ ] Set up HTTPS using AWS Certificate Manager (ACM) with Load Balancer or CloudFront.

- [ ] **CI/CD (Optional for initial deployment, but recommended)**
  - [ ] Set up a basic CI/CD pipeline (e.g., GitHub Actions, AWS CodePipeline) for automated deployments.

- [ ] **Testing and Validation**
  - [ ] Thoroughly test the deployed application in the production-like environment.
  - [ ] Check all functionalities, including authentication, data fetching, and display.
  - [ ] Perform basic security checks.

- [ ] **Monitoring and Maintenance Plan**
  - [ ] Define basic monitoring alerts (e.g., server down, high error rates).
  - [ ] Plan for regular updates and security patching.
