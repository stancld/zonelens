#!/bin/bash

USER_STRAVA_ID=$1
curl -X POST -H "Content-Type: application/json" -d '{"user_strava_id": $1, "after_timestamp": "2025-03-01T00:00:00Z"}' http://127.0.0.1:8000/api/process-activities/
