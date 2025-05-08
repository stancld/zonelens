from __future__ import annotations

import logging
import sys


def get_logger(name: str) -> logging.Logger:
	logging.basicConfig(
		level=logging.DEBUG,
		format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
		handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("strava_zones.log")],
	)

	return logging.getLogger(name)
