#!/bin/bash

API_TOKEN=$1

curl -k -X POST \
  https://127.0.0.1:8000/api/settings/custom-zones/ \
  -H "Authorization: Token $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "activity_type": "DEFAULT",
        "zones_definition": [
          {
            "name": "Z1 Endurance",
            "min_hr": 0,
            "max_hr": 138,
            "order": 1
          },
          {
            "name": "Z2 Moderate",
            "min_hr": 139,
            "max_hr": 163,
            "order": 2
          },
          {
            "name": "Z3 Tempo",
            "min_hr": 164,
            "max_hr": 173,
            "order": 3
          },
          {
            "name": "Z4 Threshold",
            "min_hr": 174,
            "max_hr": 181,
            "order": 4
          },
          {
            "name": "Z5 Anaerobic",
            "min_hr": 182,
            "max_hr": 999,
            "order": 5
          }
        ]
      }'
