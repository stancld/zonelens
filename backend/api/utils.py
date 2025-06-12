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

import calendar
import secrets

from cryptography.fernet import Fernet
from django.conf import settings


def determine_weeks_in_month(year: int, month: int) -> list[int]:
	"""Determine the ISO week numbers that fall within a given month and year.

	A week is considered part of the month if any day of that week falls in the month.
	"""
	weeks_in_month = []
	cal = calendar.Calendar()
	month_days_weeks = cal.monthdatescalendar(year, month)
	for week_days in month_days_weeks:
		for day_date in week_days:
			if day_date.year == year and day_date.month == month:
				iso_year, iso_week, _ = day_date.isocalendar()
				if iso_year == year and iso_week not in weeks_in_month:
					weeks_in_month.append(iso_week)
				break
	return weeks_in_month


def get_fernet() -> Fernet:
	"""Initializes Fernet based on the key in settings."""
	key = settings.FERNET_KEY
	if not key:
		raise ValueError("FERNET_KEY not set in settings")
	return Fernet(key)


def encrypt_data(data: str) -> str:
	"""Encrypts string data using Fernet."""
	if not data:
		return ""
	fernet = get_fernet()
	return fernet.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data: str) -> str:
	"""Decrypts string data using Fernet."""
	if not encrypted_data:
		return ""
	fernet = get_fernet()
	return fernet.decrypt(encrypted_data.encode()).decode()


def make_random_password(
	length: int = 10,
	allowed_chars: str = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789",
) -> str:
	return "".join(secrets.choice(allowed_chars) for i in range(length))
