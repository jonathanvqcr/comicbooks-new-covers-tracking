"""
alert_artists.py — Create ARTIST_COVER_ALERT notifications.

Detects when a tracked artist has a cover credit on any upcoming issue
that hasn't been alerted yet.

Runs as part of the alert pipeline after each sync.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Artist, CoverArtist, Issue, IssueCover, Notification, Series

logger = logging.getLogger(__name__)

# Track which (issue_id, artist_id) pairs have already been notified to avoid duplicates
_ALREADY_NOTIFIED_KEY = "ARTIST_COVER_ALERT"


def run_artist_alerts(db: Session | None = None) -> int:
    """
    Create ARTIST_COVER_ALERT notifications for tracked artists who have
    a cover credit on an upcoming issue, where no alert has been sent yet.

    Returns number of alerts created.
    """
    close_db = db is None
    if db is None:
        db = SessionLocal()

    try:
        today = date.today()
        lookahead = today + relativedelta(months=3)
        count = 0

        # Find all tracked artists
        tracked_artists = db.query(Artist).filter(Artist.is_tracked == True).all()  # noqa: E712
        if not tracked_artists:
            return 0

        for artist in tracked_artists:
            # Find covers by this artist on upcoming issues via explicit CoverArtist links.
            # These links are authoritative — set by the artist page scraper which knows
            # exactly which cover variant LoCG attributes to each artist.
            covers = (
                db.query(IssueCover)
                .join(CoverArtist, CoverArtist.issue_cover_id == IssueCover.id)
                .join(Issue, Issue.id == IssueCover.issue_id)
                .filter(
                    CoverArtist.artist_id == artist.id,
                    Issue.release_date >= today,
                    Issue.release_date <= lookahead,
                )
                .all()
            )

            for cover in covers:
                issue = cover.issue
                if issue is None:
                    continue

                # Check if we already have an ARTIST_COVER_ALERT for this exact (issue, artist) combo
                existing = db.query(Notification).filter(
                    Notification.type == "ARTIST_COVER_ALERT",
                    Notification.issue_id == issue.id,
                    # Encode artist name in the title to detect duplicates
                    Notification.title.contains(artist.name),
                ).first()

                if existing:
                    continue

                series_name = issue.series.name if issue.series else "Unknown"
                cover_label = cover.cover_label or "a cover"

                notif = Notification(
                    type="ARTIST_COVER_ALERT",
                    title=f"{artist.name} has {cover_label} on {series_name} #{issue.issue_number or '?'}",
                    body=(
                        f"{artist.name} is credited for {cover_label} on "
                        f"{series_name} #{issue.issue_number}"
                        + (f", releasing {issue.release_date.strftime('%B %d, %Y')}"
                           if issue.release_date else "")
                        + "."
                    ),
                    issue_id=issue.id,
                    series_id=issue.series_id,
                )
                db.add(notif)
                count += 1

        db.commit()
        if count:
            logger.info("Created %d ARTIST_COVER_ALERT notifications", count)
        return count

    finally:
        if close_db:
            db.close()
