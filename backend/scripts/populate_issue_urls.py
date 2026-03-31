"""
populate_issue_urls.py — One-time script to backfill locg_url on existing issues.

Scrapes only the series listing pages for series that have a locg_url in the DB
(same pages already fetched during sync — no per-issue detail fetches needed).

Maps locg_issue_id → issue_url from the listing, then bulk-updates issues that
are missing locg_url. Takes ~30 seconds for 3 series.

Usage:
    cd /Users/jonathanvelasquez/Code/comicbooks-new-covers-tracking
    python -m backend.scripts.populate_issue_urls
"""
from __future__ import annotations

import asyncio
import logging
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from backend.database import SessionLocal
from backend.models import Series, Issue
from backend.locg.browser import get_series_issues
from backend.locg.parsers import parse_issue

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def run():
    db = SessionLocal()
    try:
        # Get all series that have a LoCG URL
        series_list = db.query(Series).filter(
            Series.locg_url != None,
            Series.locg_url != "",
        ).all()

        logger.info("Found %d series with URLs to scrape", len(series_list))

        total_updated = 0

        for series in series_list:
            logger.info("Scraping: %s (%s)", series.name, series.locg_url)
            try:
                raw_issues = _run_async(get_series_issues(series.locg_url))
                logger.info("  Got %d issues from listing", len(raw_issues))

                for raw in raw_issues:
                    parsed = parse_issue(raw)
                    locg_id = parsed.get("locg_issue_id")
                    issue_url = parsed.get("issue_url")

                    if not locg_id or not issue_url:
                        continue

                    # Find the DB row and update if locg_url is missing
                    row = db.query(Issue).filter(
                        Issue.locg_issue_id == locg_id,
                        Issue.locg_url == None,
                    ).first()

                    if row:
                        row.locg_url = issue_url
                        total_updated += 1

                db.commit()
                logger.info("  Updated %d issues so far", total_updated)

            except Exception as exc:
                logger.error("  Failed for %s: %s", series.name, exc)
                db.rollback()

        logger.info("Done. Total issues updated: %d", total_updated)

        # Report remaining NULLs
        null_count = db.query(Issue).filter(Issue.locg_url == None).count()
        if null_count:
            logger.warning("%d issues still have no locg_url (no locg_issue_id or not in a scraped series)", null_count)
        else:
            logger.info("All issues now have locg_url populated!")

    finally:
        db.close()


if __name__ == "__main__":
    run()
