"""
scheduler.py — APScheduler setup.

Jobs:
  sync_releases    Every Monday at 7:00 AM  (weekly sync + alert pipeline)
  generate_report  Every Friday at 9:00 AM  (weekly PDF report)

Jobs are also triggerable on demand via POST /api/admin/sync-now.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _job_sync_releases():
    """Wrapper for sync_releases — catches all exceptions so scheduler stays alive."""
    try:
        from backend.jobs.sync_releases import sync_releases
        result = sync_releases()
        logger.info("Scheduled sync complete: %s", result)
    except Exception as exc:
        logger.exception("Scheduled sync_releases failed: %s", exc)


def _job_sync_series():
    """Wrapper for sync_series — catches all exceptions so scheduler stays alive."""
    try:
        from backend.jobs.sync_releases import sync_series
        result = sync_series()
        logger.info("Scheduled sync_series complete: %s", result)
    except Exception as exc:
        logger.exception("Scheduled sync_series failed: %s", exc)


def _job_sync_reprints():
    """Wrapper for sync_reprints — catches all exceptions so scheduler stays alive."""
    try:
        from backend.jobs.sync_releases import sync_reprints
        result = sync_reprints()
        logger.info("Scheduled sync_reprints complete: %s", result)
    except Exception as exc:
        logger.exception("Scheduled sync_reprints failed: %s", exc)


def _job_sync_artists():
    """Wrapper for sync_artists — catches all exceptions so scheduler stays alive."""
    try:
        from backend.jobs.sync_releases import sync_artists
        result = sync_artists()
        logger.info("Scheduled sync_artists complete: %s", result)
    except Exception as exc:
        logger.exception("Scheduled sync_artists failed: %s", exc)


def _job_generate_report():
    """Wrapper for generate_weekly_report — catches all exceptions."""
    try:
        from backend.database import SessionLocal
        from backend.models import NotificationSettings
        from backend.jobs.generate_report import generate_weekly_report

        db = SessionLocal()
        try:
            settings = db.query(NotificationSettings).filter(NotificationSettings.id == 1).first()
            email_enabled = settings.email_enabled if settings else False
            path = generate_weekly_report(db, send_email_enabled=email_enabled)
            logger.info("Scheduled report generated: %s", path)
        finally:
            db.close()
    except Exception as exc:
        logger.exception("Scheduled generate_report failed: %s", exc)


def get_scheduler() -> BackgroundScheduler:
    """Return the global scheduler instance, creating it if needed."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="America/New_York")
    return _scheduler


def start_scheduler() -> None:
    """Register all jobs and start the scheduler. Call once at app startup."""
    scheduler = get_scheduler()

    # Weekly sync — every Monday at 7:00 AM Eastern
    scheduler.add_job(
        _job_sync_releases,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="sync_releases",
        name="Weekly LoCG sync",
        replace_existing=True,
        misfire_grace_time=3600,  # allow up to 1h late if server was down
    )

    # Weekly PDF report — every Friday at 9:00 AM Eastern
    scheduler.add_job(
        _job_generate_report,
        trigger=CronTrigger(day_of_week="fri", hour=9, minute=0),
        id="generate_report",
        name="Weekly PDF report",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "Scheduler started. Next sync: %s",
        scheduler.get_job("sync_releases").next_run_time,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def trigger_sync_now() -> str:
    """
    Trigger sync_releases immediately as a one-shot job.
    Returns a job ID string.
    """
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()

    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime

    job = scheduler.add_job(
        _job_sync_releases,
        trigger=DateTrigger(run_date=datetime.now()),
        id=f"sync_now_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        name="Manual sync",
    )
    logger.info("Manual sync triggered, job_id=%s", job.id)
    return job.id


def trigger_sync_series_now() -> str:
    """Trigger sync_series immediately as a one-shot job. Returns a job ID string."""
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()

    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime

    job = scheduler.add_job(
        _job_sync_series,
        trigger=DateTrigger(run_date=datetime.now()),
        id=f"sync_series_now_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        name="Manual sync — series",
    )
    logger.info("Manual sync_series triggered, job_id=%s", job.id)
    return job.id


def trigger_sync_reprints_now() -> str:
    """Trigger sync_reprints immediately as a one-shot job. Returns a job ID string."""
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()

    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime

    job = scheduler.add_job(
        _job_sync_reprints,
        trigger=DateTrigger(run_date=datetime.now()),
        id=f"sync_reprints_now_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        name="Manual sync — reprints",
    )
    logger.info("Manual sync_reprints triggered, job_id=%s", job.id)
    return job.id


def trigger_sync_artists_now() -> str:
    """Trigger sync_artists immediately as a one-shot job. Returns a job ID string."""
    scheduler = get_scheduler()
    if not scheduler.running:
        start_scheduler()

    from apscheduler.triggers.date import DateTrigger
    from datetime import datetime

    job = scheduler.add_job(
        _job_sync_artists,
        trigger=DateTrigger(run_date=datetime.now()),
        id=f"sync_artists_now_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        name="Manual sync — artists",
    )
    logger.info("Manual sync_artists triggered, job_id=%s", job.id)
    return job.id
