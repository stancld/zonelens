# Changelog
All notable changes to this project will be documented in this file. The documented versioning starts from the public alpha release `0.3.1`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [Unreleased] - YYYY-MM-DD

### Public

#### Added
- ...

#### Changed
- ...

#### Fixed
- ...


### Internal

#### Added
- ...

#### Changed
- ...

#### Fixed
- ...


## [0.4.1] - 2025-06-30

#### Fixed
- Started listening to events with `aspect_type="update"` to catch activities sync via Garmin and other devices.


## [0.4.0] - 2025-06-22

### Public

#### Added
- Added link to the extension in Chrome store
- Added home button to HR Zones config display
- Added button routing to Strava calendar on the home screen
- Added button routing to HR Zones config on the home screen
- Added info panel displaying the initial sync status
- Enabled account deletion
- Added a toggle button to switch between time and percentage views for weekly summaries

#### Changed
- Changed time in zone calculation to be based on moving time instead of elapsed time.


### Internal

#### Added
- Added support for separate development Chrome extension ID

#### Fixed
- Used `gunicorn.conf.py` to run APScheduler in a single process only
