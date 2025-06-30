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

from datetime import UTC, datetime

from django.db import migrations


def populate_activity_processing_queue(apps, schema_editor):  # noqa: ARG001
	"""
	Add all existing users to the activity processing queue who are not already in it.
	"""
	StravaUser = apps.get_model("api", "StravaUser")
	ActivityProcessingQueue = apps.get_model("api", "ActivityProcessingQueue")

	new_queue_entries = [
		ActivityProcessingQueue(
			user=user,
			last_processed_activity_start_time=datetime(2025, 6, 14, tzinfo=UTC),
		)
		for user in StravaUser.objects.all()
	]

	if new_queue_entries:
		ActivityProcessingQueue.objects.bulk_create(new_queue_entries)


class Migration(migrations.Migration):
	dependencies = [
		("api", "0007_activityprocessingqueue_num_processed_and_more"),
	]

	operations = [
		migrations.RunPython(populate_activity_processing_queue, migrations.RunPython.noop),
	]
