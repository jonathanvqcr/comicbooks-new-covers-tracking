"""
alert_releases.py — Create RELEASE_ALERT and REPRINT_ALERT notifications.

RELEASE_ALERT: followed series issues releasing this week.
REPRINT_ALERT: any reprint of a previously-tracked issue.

Runs as part of the alert pipeline after each sync.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Issue, Notification, Series

logger = logging.getLogger(__name__)


def run_release_alerts(db: Session | None = None) -> int:
    """
    Create RELEASE_ALERT notifications for issues releasing this week
    that haven't been alerted yet.
    Also creates REPRINT_ALERT for reprints.

    Returns total number of alerts created.
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()

    try:
        today = date.today()
        week_end = today + timedelta(days=7)
        count = 0

        # ── Release alerts ──
        issues = (
            db.query(Issue)
            .join(Series, Issue.series_id == Series.id)
            .filter(
                Series.is_followed == True,  # noqa: E712
                Issue.release_date != None,
                Issue.release_date >= today,
                Issue.release_date <= week_end,
                Issue.alerted_release == False,  # noqa: E712
                Issue.is_reprint == False,  # noqa: E712
            )
            .all()
        )

        for issue in issues:
            series_name = issue.series.name if issue.series else "Unknown"
            notif = Notification(
                type="RELEASE_ALERT",
                title=f"Out this week: {series_name} #{issue.issue_number or '?'}",
                body=(
                    f"{series_name} #{issue.issue_number} releases on "
                    f"{issue.release_date.strftime('%A, %B %d, %Y')}."
                ),
                issue_id=issue.id,
                series_id=issue.series_id,
            )
            db.add(notif)
            issue.alerted_release = True
            count += 1

        # ── Reprint alerts ──
        reprints = (
            db.query(Issue)
            .join(Series, Issue.series_id == Series.id)
            .filter(
                Series.is_followed == True,  # noqa: E712
                Issue.is_reprint == True,  # noqa: E712
                Issue.alerted_release == False,  # noqa: E712
            )
            .all()
        )

        for issue in reprints:
            series_name = issue.series.name if issue.series else "Unknown"
            notif = Notification(
                type="REPRINT_ALERT",
                title=f"Reprint announced: {series_name} #{issue.issue_number or '?'}",
                body=(
                    f"A reprint of {series_name} #{issue.issue_number} has been announced"
                    + (f" — releasing {issue.release_date.strftime('%B %d, %Y')}"
                       if issue.release_date else "")
                    + "."
                ),
                issue_id=issue.id,
                series_id=issue.series_id,
            )
            db.add(notif)
            issue.alerted_release = True
            count += 1

        db.commit()
        if count:
            logger.info("Created %d RELEASE/REPRINT_ALERT notifications", count)
        return count

    finally:
        if close_db:
            db.close()
