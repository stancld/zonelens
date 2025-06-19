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

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from django.utils import timezone

from api.logging import get_logger
from api.models import ActivityProcessingQueue, StravaUser, ZoneSummary
from api.utils import determine_weeks_in_month
from api.worker import Worker

logger = get_logger(__name__)


def process_activity_queue() -> None:
	"""Process the next user in the activity processing queue and update zone summaries."""
	queue_entry = ActivityProcessingQueue.objects.order_by("updated_at").first()
	if not queue_entry:
		logger.info("Activity processing queue is empty.")
		return

	# user_profile defined early for use in logging and processing
	user_profile = queue_entry.user
	logger.info(f"Processing activities for user {user_profile.strava_id}")

	try:
		worker = Worker(user_profile.strava_id)
		after_timestamp = (
			int(queue_entry.last_processed_activity_start_time.timestamp())
			if queue_entry.last_processed_activity_start_time
			else 0
		)
		last_processed_timestamp, more_activities, processed_in_batch = (
			worker.process_user_activities(after_timestamp=after_timestamp)
		)

		if last_processed_timestamp:
			_try_update_zone_summaries_for_user_period(
				user_profile, datetime.fromtimestamp(last_processed_timestamp)
			)

		if not more_activities:
			logger.info(
				f"All activities processed for user {user_profile.strava_id}. Removing from queue."
			)
			queue_entry.delete()

			# Update Zone Summaries for current month if all activities processed
			current_time = timezone.now()
			current_year = current_time.year
			current_month = current_time.month

			needs_current_month_update = True
			if last_processed_timestamp:
				processed_date_for_current_check = datetime.fromtimestamp(last_processed_timestamp)
				if (
					processed_date_for_current_check.year == current_year
					and processed_date_for_current_check.month == current_month
				):
					needs_current_month_update = False

			if needs_current_month_update:
				_try_update_zone_summaries_for_user_period(user_profile, current_time)

		elif last_processed_timestamp:  # Implies more_activities is True
			queue_entry.last_processed_activity_start_time = timezone.make_aware(
				datetime.fromtimestamp(last_processed_timestamp)
			)
			fields_to_update = ["last_processed_activity_start_time", "updated_at"]
			if processed_in_batch > 0:
				# Ensure num_processed is not None before incrementing
				if queue_entry.num_processed is None:
					queue_entry.num_processed = 0
				queue_entry.num_processed += processed_in_batch
				fields_to_update.append("num_processed")

			queue_entry.updated_at = timezone.now()  # Touch updated_at to signify work done
			queue_entry.save(update_fields=fields_to_update)
			logger.info(
				f"Batch processed for user {user_profile.strava_id}. "
				f"Next batch will start from {queue_entry.last_processed_activity_start_time}."
			)
		# If last_processed_timestamp is None and more_activities is True,
		# it means no new activities were found in this run for this user.
		# The queue_entry is not modified in this case, will be picked up again based on updated_at

	except Exception as e_process:
		logger.exception(
			f"Error processing activities for user {user_profile.strava_id}: {e_process}"
		)
		# Move to the end of the queue to retry later by updating its timestamp
		queue_entry.updated_at = timezone.now()
		queue_entry.save()
		# Note: Zone summaries are not updated if activity processing itself fails.


def _update_zone_summaries_for_user_period(
	user_profile: StravaUser, year: int, month: int
) -> None:
	"""Ensure monthly/weekly zone summaries are updated for a user, year, and month."""
	logger.info(
		f"Scheduler: Updating zone summaries for user {user_profile.strava_id}, "
		f"period {year}-{month:02d}"
	)

	_, _monthly_created = ZoneSummary.get_or_create_summary(
		user_profile=user_profile,
		period_type=ZoneSummary.PeriodType.MONTHLY,  # type: ignore[arg-type]
		year=year,
		period_index=month,
	)

	for week_num in sorted(determine_weeks_in_month(year, month)):
		_, _weekly_created = ZoneSummary.get_or_create_summary(
			user_profile=user_profile,
			period_type=ZoneSummary.PeriodType.WEEKLY,  # type: ignore[arg-type]
			year=year,
			period_index=week_num,
			current_month_view=month,
		)
	logger.info(
		f"Scheduler: Finished updating zone summaries for user {user_profile.strava_id}, "
		f"period {year}-{month:02d}"
	)


def _try_update_zone_summaries_for_user_period(
	user_profile: StravaUser,
	processed_date: datetime,
) -> None:
	try:
		_update_zone_summaries_for_user_period(
			user_profile, processed_date.year, processed_date.month
		)
	except Exception as e:
		logger.exception(
			f"Scheduler: Error updating zone summaries for user {user_profile.strava_id} "
			f"for {processed_date.year}-{processed_date.month:02d}: {e}"
		)


def start_scheduler() -> None:  # Added return type
	"""Start the APScheduler."""
	scheduler = BackgroundScheduler()
	scheduler.add_job(process_activity_queue, "interval", minutes=1)
	scheduler.start()
	logger.info("APScheduler started with 'process_activity_queue' job.")
