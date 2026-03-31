"""
sync_releases.py — Master sync job.

Reads config/watchlist.yaml, navigates LoCG for each series and artist,
upserts issues / covers / artists into SQLite, then triggers alert jobs.

Runs weekly (Monday 7am) via APScheduler, or on demand via POST /api/admin/sync-now.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.config import load_watchlist
from backend.database import SessionLocal
from backend.locg.browser import get_series_issues, get_issue_detail, search_series, search_upcoming_reprints
from backend.locg.parsers import parse_series, parse_issue, parse_issue_covers, parse_issue_creators, parse_artist, _parse_date
from backend.models import (
    Artist, CoverArtist, IssueCover, Issue, Notification, Series, SyncLog
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async runner helper — safe to call from any sync context / thread
# ---------------------------------------------------------------------------

def _run_async(coro, timeout: float | None = None):
    """Run an async coroutine from a sync context using a new event loop.

    timeout: optional max seconds to wait before raising asyncio.TimeoutError.
    """
    loop = asyncio.new_event_loop()
    try:
        if timeout is not None:
            coro = asyncio.wait_for(coro, timeout=timeout)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# DB upsert helpers
# ---------------------------------------------------------------------------

def _upsert_series(db: Session, series_config: dict) -> Series:
    """Find or create a Series row from watchlist config."""
    name = series_config.get("name", "").strip()
    url = series_config.get("url", "").strip() or None
    priority = series_config.get("priority", "regular")

    # Try to find by name first (URL may be empty on first run)
    row = db.query(Series).filter(Series.name == name).first()
    if not row:
        row = Series(
            name=name,
            locg_url=url,
            priority=priority,
            is_followed=True,
        )
        db.add(row)
        db.flush()
    else:
        # Update URL and priority if they've changed
        if url and row.locg_url != url:
            row.locg_url = url
        if row.priority != priority:
            row.priority = priority

    db.commit()
    return row


def _upsert_issue(db: Session, raw: dict, series_id: int) -> Optional[Issue]:
    """Upsert an issue from raw browser data. Returns the Issue row or None."""
    parsed = parse_issue(raw)
    locg_id = parsed.get("locg_issue_id")
    issue_url = parsed.get("issue_url")

    if not locg_id and not issue_url:
        return None  # Not enough data to identify this issue

    # Find by locg_issue_id
    row: Optional[Issue] = None
    if locg_id:
        row = db.query(Issue).filter(Issue.locg_issue_id == locg_id).first()

    if not row:
        row = Issue(
            locg_issue_id=locg_id,
            series_id=series_id,
            issue_number=parsed.get("issue_number"),
            title=parsed.get("title"),
            release_date=parsed.get("release_date"),
            foc_date=parsed.get("foc_date"),
            is_reprint=parsed.get("is_reprint", False),
            cover_image_url=parsed.get("cover_image_url"),
            locg_url=parsed.get("issue_url"),
        )
        db.add(row)
        db.flush()
    else:
        # Update fields that may have changed
        if parsed.get("release_date") and not row.release_date:
            row.release_date = parsed["release_date"]
        if parsed.get("foc_date") and not row.foc_date:
            row.foc_date = parsed["foc_date"]
        if parsed.get("cover_image_url") and not row.cover_image_url:
            row.cover_image_url = parsed["cover_image_url"]
        if parsed.get("title") and not row.title:
            row.title = parsed["title"]
        if parsed.get("issue_number") and not row.issue_number:
            row.issue_number = parsed["issue_number"]
        if parsed.get("issue_url") and not row.locg_url:
            row.locg_url = parsed["issue_url"]

    db.commit()
    return row


def _upsert_covers(db: Session, raw_detail: dict, issue_id: int) -> int:
    """
    Upsert cover variants and their artist credits for an issue.
    Returns number of covers upserted.
    """
    raw_covers = parse_issue_covers(raw_detail)
    all_creators = parse_issue_creators(raw_detail)
    count = 0

    for cover_data in raw_covers:
        locg_cover_id = cover_data.get("locg_cover_id")

        # Find or create cover
        cover: Optional[IssueCover] = None
        if locg_cover_id:
            cover = db.query(IssueCover).filter(
                IssueCover.locg_cover_id == locg_cover_id
            ).first()

        if not cover:
            # Check by label + issue_id if no locg_cover_id
            if cover_data.get("cover_label"):
                cover = db.query(IssueCover).filter(
                    IssueCover.issue_id == issue_id,
                    IssueCover.cover_label == cover_data["cover_label"],
                ).first()

        # Derive image URL from locg_cover_id if scraper didn't return one
        scraped_image_url = cover_data.get("cover_image_url")
        derived_image_url = (
            f"https://s3.amazonaws.com/comicgeeks/comics/covers/medium-{locg_cover_id}.jpg"
            if locg_cover_id and not scraped_image_url else scraped_image_url
        )

        if not cover:
            cover = IssueCover(
                issue_id=issue_id,
                locg_cover_id=locg_cover_id,
                cover_label=cover_data.get("cover_label"),
                cover_image_url=derived_image_url,
            )
            db.add(cover)
            db.flush()
            count += 1
        else:
            if derived_image_url and not cover.cover_image_url:
                cover.cover_image_url = derived_image_url
            if cover_data.get("cover_label") and not cover.cover_label:
                cover.cover_label = cover_data["cover_label"]

        db.commit()

        # Upsert artists on this cover
        cover_specific_artists = cover_data.get("artists") or []
        # Fall back to all issue creators if no cover-specific artists
        artists_to_link = cover_specific_artists or (all_creators if not raw_covers[0].get("artists") else [])

        # If still no artists, check if any tracked artist name appears in the cover label
        # (LoCG embeds artist names in cover labels, e.g. "Cover B Dan Mora Variant")
        if not artists_to_link and cover_data.get("cover_label"):
            label_lower = cover_data["cover_label"].lower()
            tracked = db.query(Artist).filter(Artist.is_tracked == True).all()  # noqa: E712
            for ta in tracked:
                if ta.name.lower() in label_lower:
                    artists_to_link = [{"name": ta.name, "locg_creator_id": ta.locg_creator_id, "locg_url": ta.locg_url}]
                    logger.debug("  Matched tracked artist '%s' in cover label: %s", ta.name, cover_data["cover_label"])

        for artist_data in artists_to_link:
            artist = _upsert_artist(db, artist_data)
            if not artist:
                continue
            # Link if not already linked
            existing_link = db.query(CoverArtist).filter(
                CoverArtist.issue_cover_id == cover.id,
                CoverArtist.artist_id == artist.id,
            ).first()
            if not existing_link:
                db.add(CoverArtist(issue_cover_id=cover.id, artist_id=artist.id))
                db.commit()

    return count


def _upsert_artist(db: Session, artist_data: dict) -> Optional[Artist]:
    """Find or create an Artist row. Returns Artist or None."""
    name = (artist_data.get("name") or "").strip()
    if not name:
        return None

    locg_creator_id = artist_data.get("locg_creator_id")

    artist: Optional[Artist] = None
    if locg_creator_id:
        artist = db.query(Artist).filter(Artist.locg_creator_id == locg_creator_id).first()
    if not artist:
        artist = db.query(Artist).filter(Artist.name == name).first()

    if not artist:
        artist = Artist(
            name=name,
            locg_creator_id=locg_creator_id,
            locg_url=artist_data.get("locg_url"),
            is_tracked=False,  # Only tracked if explicitly in watchlist.yaml
        )
        db.add(artist)
        db.flush()
        db.commit()
    else:
        if locg_creator_id and not artist.locg_creator_id:
            artist.locg_creator_id = locg_creator_id
            db.commit()

    return artist


def _sync_artists_from_watchlist(db: Session, artist_configs: list[dict]) -> None:
    """
    Ensure all artists in watchlist.yaml exist in the DB and are marked is_tracked=True.
    Artists removed from the watchlist are NOT deleted — they just lose is_tracked.
    """
    # Mark all currently-tracked artists as untracked first (will re-enable below)
    db.query(Artist).filter(Artist.is_tracked == True).update({"is_tracked": False})  # noqa: E712
    db.commit()

    for artist_config in artist_configs:
        name = artist_config.get("name", "").strip()
        if not name:
            continue
        url = artist_config.get("url", "").strip() or None

        artist = db.query(Artist).filter(Artist.name == name).first()
        if not artist:
            artist = Artist(
                name=name,
                locg_url=url,
                is_tracked=True,
            )
            db.add(artist)
        else:
            artist.is_tracked = True
            if url and not artist.locg_url:
                artist.locg_url = url

    db.commit()


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

def sync_releases() -> dict:
    """
    Main weekly sync job.

    1. Read watchlist.yaml
    2. For each series with a URL: fetch issues, upsert to DB
    3. For new issues or those missing FOC: fetch detail, upsert covers + artists
    4. Sync tracked artists from watchlist
    5. Log to sync_log

    Returns summary dict for logging.
    """
    db = SessionLocal()
    log = SyncLog(job_name="sync_releases", status="running")
    db.add(log)
    db.commit()

    total_fetched = 0
    total_inserted = 0
    errors: list[str] = []

    try:
        watchlist = load_watchlist()
        series_configs = watchlist.get("series", [])
        artist_configs = watchlist.get("artists", [])

        logger.info("Starting sync: %d series, %d artists", len(series_configs), len(artist_configs))

        # ── Sync each series ──
        for series_config in series_configs:
            name = series_config.get("name", "Unknown")
            url = series_config.get("url", "").strip()

            if not url:
                logger.warning("Series '%s' has no URL in watchlist.yaml — skipping", name)
                continue

            try:
                series = _upsert_series(db, series_config)

                # Fetch issue list from LoCG
                logger.info("Fetching issues for: %s", name)
                raw_issues = _run_async(get_series_issues(url))
                total_fetched += len(raw_issues)
                logger.info("  Found %d issues", len(raw_issues))

                for raw_issue in raw_issues:
                    issue = _upsert_issue(db, raw_issue, series.id)
                    if issue is None:
                        continue

                    # Skip detail fetch for issues released more than 4 weeks ago —
                    # they'll never appear in the FOC window and aren't worth scraping.
                    release_date = issue.release_date
                    if release_date and release_date < (date.today() - timedelta(weeks=4)):
                        continue

                    # Fetch detail for issues missing FOC date, covers, or any cover image
                    needs_detail = (
                        issue.foc_date is None or
                        db.query(IssueCover).filter(IssueCover.issue_id == issue.id).count() == 0 or
                        db.query(IssueCover).filter(
                            IssueCover.issue_id == issue.id,
                            IssueCover.cover_image_url == None,
                        ).count() > 0
                    )
                    issue_url = raw_issue.get("issue_url")

                    if needs_detail and issue_url:
                        try:
                            logger.debug("  Fetching detail for issue %s", issue.issue_number)
                            raw_detail = _run_async(get_issue_detail(issue_url), timeout=90)

                            # Capture issue URL from detail if not already stored
                            if raw_detail.get("issue_url") and not issue.locg_url:
                                issue.locg_url = raw_detail["issue_url"]
                                db.commit()

                            # Update FOC date
                            foc = _parse_date(raw_detail.get("foc_date_raw"))

                            # Sanity check: FOC must be before the release date.
                            # Year-less date inference can place it 1 year too late;
                            # if so, subtract a year to correct.
                            if foc and issue.release_date and foc > issue.release_date:
                                try:
                                    foc_corrected = foc.replace(year=foc.year - 1)
                                    if foc_corrected <= issue.release_date:
                                        foc = foc_corrected
                                    else:
                                        foc = None  # Can't resolve year ambiguity
                                except (ValueError, OverflowError):
                                    foc = None

                            if foc and not issue.foc_date:
                                issue.foc_date = foc
                                db.commit()

                            # Update release date
                            rel = _parse_date(raw_detail.get("release_date_raw"))
                            if rel and not issue.release_date:
                                issue.release_date = rel
                                db.commit()

                            # Upsert covers
                            covers_added = _upsert_covers(db, raw_detail, issue.id)
                            total_inserted += covers_added

                        except Exception as detail_exc:
                            logger.warning("  Detail fetch failed for %s#%s: %s",
                                           name, issue.issue_number, detail_exc)

            except Exception as series_exc:
                msg = f"Series '{name}': {series_exc}"
                logger.error(msg)
                errors.append(msg)
                continue

        # ── Scan for upcoming reprints ──
        # For each tracked series × next 12 weeks: navigate LoCG new-comics page,
        # search "{series} printing", upsert any new reprint issues found.
        logger.info("Scanning for upcoming reprints: %d series × 12 weeks", len(series_configs))
        today = date.today()
        # Generate Wednesday dates for next 12 weeks (new comic release day)
        days_to_wednesday = (2 - today.weekday()) % 7
        first_wednesday = today + timedelta(days=days_to_wednesday if days_to_wednesday else 7)
        week_dates = [first_wednesday + timedelta(weeks=i) for i in range(12)]

        reprint_keywords = {"printing", "reprint", "facsimile", "2nd print", "second print", "3rd print"}

        for series_config in series_configs:
            series_name = series_config.get("name", "Unknown")
            if not series_config.get("url", "").strip():
                continue

            series_row = db.query(Series).filter(Series.name == series_name).first()
            if not series_row:
                continue

            for week_date in week_dates:
                date_str = week_date.strftime("%Y-%m-%d")
                try:
                    raw_reprints = _run_async(
                        search_upcoming_reprints(series_name, date_str), timeout=90
                    )

                    for raw in raw_reprints:
                        title = (raw.get("title") or "").lower()
                        # Only process entries that look like reprints
                        if not any(kw in title for kw in reprint_keywords):
                            continue

                        locg_id = raw.get("locg_issue_id")
                        display_title = raw.get("title") or f"{series_name} reprint"

                        # Parse issue number from title if not already present (e.g. "Absolute Batman #8 4th Printing" → "8")
                        if not raw.get("issue_number"):
                            import re as _re
                            m = _re.search(r'#(\w+)', raw.get("title") or "")
                            if m:
                                raw["issue_number"] = m.group(1)

                        # LoCG reuses the same locg_issue_id for reprints — check if issue exists
                        existing_issue = (
                            db.query(Issue).filter(Issue.locg_issue_id == locg_id).first()
                            if locg_id else None
                        )

                        if existing_issue:
                            issue = existing_issue
                        else:
                            # New issue not yet in DB — upsert it as a reprint
                            raw["is_reprint"] = True
                            issue = _upsert_issue(db, raw, series_row.id)
                            if not issue:
                                continue
                            total_inserted += 1

                        # One REPRINT_ALERT per variant — dedup by (issue_id, cover_image_url)
                        reprint_cover_url = raw.get("cover_image_url")
                        existing_alert = db.query(Notification).filter(
                            Notification.type == "REPRINT_ALERT",
                            Notification.issue_id == issue.id,
                            Notification.cover_image_url == reprint_cover_url,
                        ).first()
                        if not existing_alert:
                            db.add(Notification(
                                type="REPRINT_ALERT",
                                title=f"Reprint announced: {display_title}",
                                body=f"{series_name} — FOC: {issue.foc_date or 'TBD'}, Release: {issue.release_date or 'TBD'}",
                                cover_image_url=raw.get("cover_image_url"),
                                reprint_date=week_date,
                                issue_id=issue.id,
                                series_id=series_row.id,
                            ))
                            db.commit()
                            logger.info("REPRINT_ALERT created for %s", display_title)

                except Exception as reprint_exc:
                    logger.warning(
                        "Reprint scan failed: series=%s week=%s error=%s",
                        series_name, date_str, reprint_exc
                    )

        # ── Sync tracked artists ──
        _sync_artists_from_watchlist(db, artist_configs)

        status = "partial" if errors else "success"
        log.status = status
        log.records_fetched = total_fetched
        log.records_inserted = total_inserted
        if errors:
            log.error_message = "; ".join(errors[:3])
        log.finished_at = datetime.utcnow()
        db.commit()

        logger.info("Sync complete: status=%s fetched=%d inserted=%d errors=%d",
                    status, total_fetched, total_inserted, len(errors))

        # ── Run alert pipeline ──
        _run_alert_pipeline(db)

        return {
            "status": status,
            "records_fetched": total_fetched,
            "records_inserted": total_inserted,
            "errors": errors,
        }

    except Exception as exc:
        logger.exception("Fatal error in sync_releases: %s", exc)
        import traceback
        log.status = "error"
        log.error_message = str(exc)
        log.error_detail = traceback.format_exc()
        log.finished_at = datetime.utcnow()
        db.commit()

        # Create in-app SYNC_ERROR notification
        db.add(Notification(
            type="SYNC_ERROR",
            title="Sync failed — LoCG scraper error",
            body=str(exc),
        ))
        db.commit()
        raise

    finally:
        db.close()


def _run_alert_pipeline(db: Session) -> None:
    """Run all alert jobs after a successful sync."""
    from backend.jobs.alert_foc import run_foc_alerts
    from backend.jobs.alert_releases import run_release_alerts
    from backend.jobs.alert_artists import run_artist_alerts
    try:
        run_foc_alerts(db)
    except Exception as exc:
        logger.error("alert_foc failed: %s", exc)
    try:
        run_release_alerts(db)
    except Exception as exc:
        logger.error("alert_releases failed: %s", exc)
    try:
        run_artist_alerts(db)
    except Exception as exc:
        logger.error("alert_artists failed: %s", exc)
