"""
alert_foc.py — Create FOC_ALERT notifications for issues with FOC date within N days.

Runs as part of the alert pipeline after each sync.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Issue, Notification, NotificationSettings, Series

logger = logging.getLogger(__name__)


def run_foc_alerts(db: Session | None = None) -> int:
    """
    Create FOC_ALERT notifications for issues whose FOC date is within
    `foc_alert_days` days and haven't been alerted yet.

    Returns number of alerts created.
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()

    try:
        settings = db.query(NotificationSettings).filter(NotificationSettings.id == 1).first()
        alert_days = settings.foc_alert_days if settings else 14
        today = date.today()
        cutoff = today + timedelta(days=alert_days)

        issues = (
            db.query(Issue)
            .join(Series, Issue.series_id == Series.id)
            .filter(
                Series.is_followed == True,  # noqa: E712
                Issue.foc_date != None,
                Issue.foc_date >= today,
                Issue.foc_date <= cutoff,
                Issue.alerted_foc == False,  # noqa: E712
            )
            .all()
        )

        count = 0
        for issue in issues:
            days_until = (issue.foc_date - today).days
            series_name = issue.series.name if issue.series else "Unknown"

            if days_until == 0:
                urgency = "TODAY"
            elif days_until == 1:
                urgency = "TOMORROW"
            else:
                urgency = f"in {days_until} days"

            notif = Notification(
                type="FOC_ALERT",
                title=f"FOC {urgency}: {series_name} #{issue.issue_number or '?'}",
                body=(
                    f"Final Order Cutoff for {series_name} #{issue.issue_number} "
                    f"is {issue.foc_date.strftime('%B %d, %Y')} ({urgency}). "
                    f"Release date: {issue.release_date.strftime('%B %d, %Y') if issue.release_date else 'TBD'}."
                ),
                issue_id=issue.id,
                series_id=issue.series_id,
            )
            db.add(notif)
            issue.alerted_foc = True
            count += 1

        db.commit()
        if count:
            logger.info("Created %d FOC_ALERT notifications", count)
        return count

    finally:
        if close_db:
            db.close()
