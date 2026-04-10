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
from backend.locg.browser import get_series_issues, get_issue_detail, get_artist_upcoming_issues, search_series, search_upcoming_reprints
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
        if parsed.get("issue_url"):
            new_url = parsed["issue_url"]
            locg_id_str = str(locg_id) if locg_id else ""
            # Never store a variant URL (?variant= or /cover-X- slug) as the canonical locg_url
            is_variant_url = "?variant=" in new_url or (
                locg_id_str and locg_id_str not in new_url
            )
            if not is_variant_url:
                if not row.locg_url or (locg_id_str and locg_id_str in new_url and locg_id_str not in row.locg_url):
                    row.locg_url = new_url

    db.commit()
    return row


def _upsert_covers(db: Session, raw_detail: dict, issue_id: int) -> int:
    """
    Upsert cover variants and their artist credits for an issue.
    Returns number of new covers added.
    Creates a COVER_UPDATE_ALERT notification when new variants are discovered.
    """
    raw_covers = parse_issue_covers(raw_detail)
    all_creators = parse_issue_creators(raw_detail)
    count = 0
    new_labels: list[str] = []

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
            if cover_data.get("cover_label"):
                new_labels.append(cover_data["cover_label"])
        else:
            if derived_image_url and not cover.cover_image_url:
                cover.cover_image_url = derived_image_url
            if cover_data.get("cover_label"):
                cover.cover_label = cover_data["cover_label"]  # always update — labels get refined over time

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

    # Fire a COVER_UPDATE_ALERT if new variants were discovered on an existing issue
    if count > 0:
        issue = db.query(Issue).filter(Issue.id == issue_id).first()
        series_name = issue.series.name if issue and issue.series else ""
        issue_num = issue.issue_number if issue else "?"
        series_id = issue.series_id if issue else None
        plural = "s" if count > 1 else ""
        title = f"New covers announced: {series_name} #{issue_num} ({count} new variant{plural})"
        body = "\n".join(new_labels) if new_labels else None
        # Dedup: skip if we already have an identical COVER_UPDATE_ALERT for this issue
        already = db.query(Notification).filter(
            Notification.type == "COVER_UPDATE_ALERT",
            Notification.issue_id == issue_id,
            Notification.title == title,
        ).first()
        if not already:
            db.add(Notification(
                type="COVER_UPDATE_ALERT",
                title=title,
                body=body,
                issue_id=issue_id,
                series_id=series_id,
                is_read=False,
            ))
            db.commit()
            logger.info("COVER_UPDATE_ALERT: %s", title)

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
# Phase helpers
# ---------------------------------------------------------------------------

def _phase_series(db: Session, series_configs: list[dict], totals: dict, errors: list[str]) -> None:
    """Sync each series from the watchlist."""
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
            totals["fetched"] += len(raw_issues)
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

                # Fetch detail for issues missing FOC date, covers, or any cover image,
                # OR for any upcoming issue (foc_date >= today) so newly announced covers are picked up
                needs_detail = (
                    issue.foc_date is None or
                    db.query(IssueCover).filter(IssueCover.issue_id == issue.id).count() == 0 or
                    db.query(IssueCover).filter(
                        IssueCover.issue_id == issue.id,
                        IssueCover.cover_image_url == None,
                    ).count() > 0 or
                    (issue.foc_date is not None and issue.foc_date >= date.today())
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
                        totals["inserted"] += covers_added

                    except Exception as detail_exc:
                        logger.warning("  Detail fetch failed for %s#%s: %s",
                                       name, issue.issue_number, detail_exc)

        except Exception as series_exc:
            msg = f"Series '{name}': {series_exc}"
            logger.error(msg)
            errors.append(msg)
            continue


def _phase_reprints(db: Session, series_configs: list[dict], totals: dict, errors: list[str]) -> None:
    """Scan for upcoming reprints across all tracked series."""
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
                        totals["inserted"] += 1

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


def _phase_artists(db: Session, artist_configs: list[dict], totals: dict, errors: list[str]) -> None:
    """Sync tracked artists from watchlist and scrape their profile pages."""
    # ── Sync tracked artists from watchlist ──
    _sync_artists_from_watchlist(db, artist_configs)

    # ── Sync tracked artist profile pages (discovers covers on non-watchlist series) ──
    tracked_artists_with_url = (
        db.query(Artist)
        .filter(Artist.is_tracked == True, Artist.locg_url != None)  # noqa: E712
        .all()
    )
    logger.info("Syncing artist profile pages for %d tracked artists", len(tracked_artists_with_url))

    for artist in tracked_artists_with_url:
        try:
            logger.info("  Artist profile: %s (%s)", artist.name, artist.locg_url)
            raw_artist_issues = _run_async(
                get_artist_upcoming_issues(artist.locg_url), timeout=60
            )
            logger.info("    Found %d issues on artist page", len(raw_artist_issues))
            totals["fetched"] += len(raw_artist_issues)

            for raw_issue in raw_artist_issues:
                issue_url = raw_issue.get("issue_url")
                if not issue_url:
                    continue

                locg_issue_id = raw_issue.get("locg_issue_id")

                # Check if we already have this issue (and it's in a followed series) —
                # if so, skip the expensive detail fetch; covers already populated by series sync.
                existing = db.query(Issue).filter(Issue.locg_issue_id == locg_issue_id).first() if locg_issue_id else None
                if existing and existing.series and existing.series.is_followed:
                    # Still ensure artist-cover link exists on the already-synced issue
                    issue = existing
                    for cover in db.query(IssueCover).filter(IssueCover.issue_id == issue.id).all():
                        if cover.cover_label and artist.name.lower() in cover.cover_label.lower():
                            existing_link = db.query(CoverArtist).filter(
                                CoverArtist.issue_cover_id == cover.id,
                                CoverArtist.artist_id == artist.id,
                            ).first()
                            if not existing_link:
                                db.add(CoverArtist(issue_cover_id=cover.id, artist_id=artist.id))
                                db.commit()
                                logger.info("    Linked %s → %s (followed series)", artist.name, cover.cover_label)
                    already_linked = db.query(CoverArtist).join(IssueCover).filter(
                        IssueCover.issue_id == issue.id,
                        CoverArtist.artist_id == artist.id,
                    ).first()
                    if not already_linked:
                        cover_a = db.query(IssueCover).filter(
                            IssueCover.issue_id == issue.id,
                            IssueCover.cover_label == "Cover A",
                        ).first()
                        if not cover_a:
                            cover_a = IssueCover(
                                issue_id=issue.id,
                                cover_label="Cover A",
                                cover_image_url=issue.cover_image_url,
                            )
                            db.add(cover_a)
                            db.flush()
                            db.commit()
                        db.add(CoverArtist(issue_cover_id=cover_a.id, artist_id=artist.id))
                        db.commit()
                        logger.info("    Linked %s → Cover A for %s (followed series)", artist.name, issue.title)
                    continue

                # Fetch full detail to get series info, FOC date, and cover variants
                try:
                    raw_detail = _run_async(get_issue_detail(issue_url), timeout=90)
                except Exception as det_exc:
                    logger.warning("    Detail fetch failed for %s: %s", issue_url, det_exc)
                    continue

                # Get / create the series (not followed — won't pollute FOC Calendar)
                series_name = raw_detail.get("series_name") or raw_issue.get("title", "").split(" #")[0].strip()
                locg_series_id = raw_detail.get("locg_series_id")
                series_url = raw_detail.get("series_url")

                if not series_name and not locg_series_id:
                    logger.warning("    No series info for %s, skipping", issue_url)
                    continue

                # Skip LoCG placeholder series used for unconfirmed/community-submitted covers
                _PLACEHOLDER_SERIES = {"submit new variant cover", "submit a cover", "variant covers"}
                if series_name and series_name.lower() in _PLACEHOLDER_SERIES:
                    logger.info("    Skipping placeholder series '%s' for %s", series_name, issue_url)
                    continue

                # Find existing series
                series = None
                if locg_series_id:
                    series = db.query(Series).filter(Series.locg_series_id == locg_series_id).first()
                if not series and series_name:
                    series = db.query(Series).filter(Series.name == series_name).first()
                if not series:
                    series = Series(
                        name=series_name,
                        locg_series_id=locg_series_id,
                        locg_url=series_url,
                        is_followed=False,  # artist-tracked only, not in watchlist
                    )
                    db.add(series)
                    db.flush()
                    db.commit()
                    logger.info("    Created artist-tracked series: %s", series_name)

                # Upsert the issue under that series
                issue = existing or _upsert_issue(db, raw_detail, series.id)
                if not issue:
                    continue

                # Update FOC / release dates if missing
                foc = _parse_date(raw_detail.get("foc_date_raw"))
                if foc and issue.release_date and foc > issue.release_date:
                    try:
                        foc_corrected = foc.replace(year=foc.year - 1)
                        foc = foc_corrected if foc_corrected <= issue.release_date else None
                    except (ValueError, OverflowError):
                        foc = None
                if foc and not issue.foc_date:
                    issue.foc_date = foc
                    db.commit()
                rel = _parse_date(raw_detail.get("release_date_raw"))
                # Fallback: parse release date from the li text returned by get_comics API
                if not rel and raw_issue.get("date_text"):
                    rel = _parse_date(raw_issue["date_text"])
                if rel and not issue.release_date:
                    issue.release_date = rel
                    db.commit()
                if raw_detail.get("issue_url") and not issue.locg_url:
                    issue.locg_url = raw_detail["issue_url"]
                    db.commit()

                # Upsert covers (creates COVER_UPDATE_ALERT for new variants automatically)
                covers_added = _upsert_covers(db, raw_detail, issue.id)
                totals["inserted"] += covers_added

                # Ensure the artist is linked to covers that match their name in the label
                for cover in db.query(IssueCover).filter(IssueCover.issue_id == issue.id).all():
                    if cover.cover_label and artist.name.lower() in cover.cover_label.lower():
                        existing_link = db.query(CoverArtist).filter(
                            CoverArtist.issue_cover_id == cover.id,
                            CoverArtist.artist_id == artist.id,
                        ).first()
                        if not existing_link:
                            db.add(CoverArtist(issue_cover_id=cover.id, artist_id=artist.id))
                            db.commit()
                            logger.info("    Linked %s → %s", artist.name, cover.cover_label)

                # If the artist still has no cover link on this issue, they are the Cover A
                # (primary) artist — LoCG doesn't list Cover A in the variant section.
                # Create a Cover A entry using the issue's main cover image and link them.
                already_linked = db.query(CoverArtist).join(IssueCover).filter(
                    IssueCover.issue_id == issue.id,
                    CoverArtist.artist_id == artist.id,
                ).first()
                if not already_linked:
                    cover_a = db.query(IssueCover).filter(
                        IssueCover.issue_id == issue.id,
                        IssueCover.cover_label == "Cover A",
                    ).first()
                    if not cover_a:
                        cover_a = IssueCover(
                            issue_id=issue.id,
                            cover_label="Cover A",
                            cover_image_url=issue.cover_image_url,
                        )
                        db.add(cover_a)
                        db.flush()
                        db.commit()
                    db.add(CoverArtist(issue_cover_id=cover_a.id, artist_id=artist.id))
                    db.commit()
                    logger.info("    Linked %s → Cover A for %s #%s", artist.name, series_name, issue.issue_number)

        except Exception as artist_exc:
            msg = f"Artist profile sync '{artist.name}': {artist_exc}"
            logger.error(msg)
            errors.append(msg)


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

    totals = {"fetched": 0, "inserted": 0}
    errors: list[str] = []

    try:
        watchlist = load_watchlist()
        series_configs = watchlist.get("series", [])
        artist_configs = watchlist.get("artists", [])

        logger.info("Starting sync: %d series, %d artists", len(series_configs), len(artist_configs))

        _phase_series(db, series_configs, totals, errors)
        _phase_reprints(db, series_configs, totals, errors)
        _phase_artists(db, artist_configs, totals, errors)

        status = "partial" if errors else "success"
        log.status = status
        log.records_fetched = totals["fetched"]
        log.records_inserted = totals["inserted"]
        if errors:
            log.error_message = "; ".join(errors[:3])
        log.finished_at = datetime.utcnow()
        db.commit()

        logger.info("Sync complete: status=%s fetched=%d inserted=%d errors=%d",
                    status, totals["fetched"], totals["inserted"], len(errors))

        # ── Run alert pipeline ──
        _run_alert_pipeline(db)

        return {
            "status": status,
            "records_fetched": totals["fetched"],
            "records_inserted": totals["inserted"],
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


# ---------------------------------------------------------------------------
# Standalone per-phase sync functions
# ---------------------------------------------------------------------------

def _run_phase_job(job_name: str, phase_fn, watchlist_key: str) -> dict:
    """
    Generic helper: create a SyncLog, run one phase helper, run the alert pipeline,
    and return a summary dict.

    phase_fn signature: (db, configs, totals, errors) -> None
    watchlist_key: "series" or "artists"
    """
    db = SessionLocal()
    log = SyncLog(job_name=job_name, status="running")
    db.add(log)
    db.commit()

    totals = {"fetched": 0, "inserted": 0}
    errors: list[str] = []

    try:
        watchlist = load_watchlist()
        configs = watchlist.get(watchlist_key, [])

        logger.info("Starting %s: %d configs", job_name, len(configs))
        phase_fn(db, configs, totals, errors)

        status = "partial" if errors else "success"
        log.status = status
        log.records_fetched = totals["fetched"]
        log.records_inserted = totals["inserted"]
        if errors:
            log.error_message = "; ".join(errors[:3])
        log.finished_at = datetime.utcnow()
        db.commit()

        logger.info("%s complete: status=%s fetched=%d inserted=%d errors=%d",
                    job_name, status, totals["fetched"], totals["inserted"], len(errors))

        _run_alert_pipeline(db)

        return {
            "status": status,
            "records_fetched": totals["fetched"],
            "records_inserted": totals["inserted"],
            "errors": errors,
        }

    except Exception as exc:
        logger.exception("Fatal error in %s: %s", job_name, exc)
        import traceback
        log.status = "error"
        log.error_message = str(exc)
        log.error_detail = traceback.format_exc()
        log.finished_at = datetime.utcnow()
        db.commit()

        db.add(Notification(
            type="SYNC_ERROR",
            title=f"Sync failed — {job_name}",
            body=str(exc),
        ))
        db.commit()
        raise

    finally:
        db.close()


def sync_series() -> dict:
    """Standalone sync: series phase only."""
    return _run_phase_job("sync_series", _phase_series, "series")


def sync_reprints() -> dict:
    """Standalone sync: reprints phase only."""
    return _run_phase_job("sync_reprints", _phase_reprints, "series")


def sync_artists() -> dict:
    """Standalone sync: artists phase only."""
    return _run_phase_job("sync_artists", _phase_artists, "artists")


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
