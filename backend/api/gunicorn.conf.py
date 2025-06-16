from __future__ import annotations

"""Gunicorn configuration file."""

# Gunicorn server hooks
# https://docs.gunicorn.org/en/stable/settings.html#server-hooks


def when_ready(server):
	"""
	Called just before the master process forks worker processes.
	This is the ideal place to start background tasks that should only run once.
	"""
	import django

	django.setup()

	from api import scheduler

	scheduler.start_scheduler()
	server.log.info("Scheduler has been started in the master process.")
