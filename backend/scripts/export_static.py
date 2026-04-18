"""
Export dashboard data to static JSON files for Vercel deployment.

Usage: backend/.venv/bin/python3 backend/scripts/export_static.py

Writes 5 files to frontend/public/data/:
  foc-export.json      — FocExportRow[]
  reprints.json        — FocExportRow[]
  upcoming-issues.json — IssueRead[]
  notifications.json   — NotificationRead[] (recent, no SYNC_ERRORs)
  sync-info.json       — SyncLogRead (the most recent sync log entry)
"""
import json
import os
import sys
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta

# Run from project root: backend/.venv/bin/python3 backend/scripts/export_static.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import or_
from backend.database import SessionLocal
from backend.models import Issue, Series, IssueCover, Notification, SyncLog, CoverArtist, Artist
from backend.schemas import (
    IssueRead, IssueCoverRead, FocExportRow, CoverVariantItem, SyncLogRead, NotificationRead
)

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "public", "data"
)


def _write(filename: str, data: object) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"  wrote {path}")


def _build_issue_read(issue: Issue) -> IssueRead:
    covers = []
    for cover in issue.covers:
        artist_names = [ca.artist.name for ca in cover.cover_artists if ca.artist]
        cover_locg_url = (
            f"{issue.locg_url}?variant={cover.locg_cover_id}"
            if issue.locg_url and cover.locg_cover_id else None
        )
        covers.append(IssueCoverRead(
            id=cover.id,
            cover_label=cover.cover_label,
            cover_image_url=cover.cover_image_url,
            artist_names=artist_names,
            locg_url=cover_locg_url,
        ))

    has_tracked_artist = any(
        ca.artist.is_tracked
        for cover in issue.covers
        for ca in cover.cover_artists
        if ca.artist
    )

    return IssueRead(
        id=issue.id,
        locg_issue_id=issue.locg_issue_id,
        series_id=issue.series_id,
        series_name=issue.series.name if issue.series else "",
        issue_number=issue.issue_number,
        title=issue.title,
        release_date=issue.release_date,
        foc_date=issue.foc_date,
        is_reprint=issue.is_reprint,
        cover_image_url=issue.cover_image_url,
        locg_url=issue.locg_url,
        covers=covers,
        has_tracked_artist=has_tracked_artist,
    )


def export_tracked_artists(db) -> None:
    artists = (
        db.query(Artist)
        .filter(Artist.is_tracked == True)
        .order_by(Artist.name.asc())
        .all()
    )
    data = [{"name": a.name, "locg_url": a.locg_url} for a in artists]
    _write("tracked-artists.json", data)
    print(f"    {len(data)} tracked artists")


def export_artist_alerts(db) -> None:
    """Issues with tracked artist covers — includes 6-week lookback for retailer exclusives."""
    from sqlalchemy import and_
    cutoff = date.today() + relativedelta(months=3)
    lookback = date.today() - timedelta(weeks=6)

    issues = (
        db.query(Issue)
        .join(IssueCover, IssueCover.issue_id == Issue.id)
        .join(CoverArtist, CoverArtist.issue_cover_id == IssueCover.id)
        .join(Artist, Artist.id == CoverArtist.artist_id)
        .filter(
            Artist.is_tracked == True,
            or_(
                and_(Issue.foc_date >= lookback, Issue.foc_date <= cutoff),
                and_(Issue.release_date >= lookback, Issue.release_date <= cutoff),
            ),
        )
        .distinct()
        .all()
    )
    issues.sort(key=lambda i: (i.foc_date or i.release_date or date.max))
    data = [_build_issue_read(i).model_dump(mode="json") for i in issues]
    _write("artist-alerts.json", data)
    print(f"    {len(data)} artist alert issues")


def export_upcoming_issues(db) -> None:
    cutoff = date.today() + relativedelta(months=3)
    today = date.today()

    # Issues from followed (watchlist) series
    followed = (
        db.query(Issue)
        .join(Issue.series)
        .filter(
            Series.is_followed == True,
            or_(Issue.foc_date <= cutoff, Issue.release_date <= cutoff),
            or_(Issue.foc_date >= today, Issue.release_date >= today),
        )
        .all()
    )

    merged = sorted(followed, key=lambda i: (i.foc_date or date.max))
    data = [_build_issue_read(i).model_dump(mode="json") for i in merged]
    _write("upcoming-issues.json", data)
    print(f"    {len(data)} upcoming issues")


def export_foc(db) -> None:
    cutoff = date.today() + relativedelta(months=3)
    issues = (
        db.query(Issue)
        .join(Issue.series)
        .filter(
            Series.is_followed == True,
            Issue.foc_date != None,
            Issue.foc_date >= date.today(),
            Issue.foc_date <= cutoff,
        )
        .order_by(Issue.foc_date.asc())
        .all()
    )

    rows = []
    for issue in issues:
        cover_a = []
        if issue.cover_image_url:
            cover_a = [CoverVariantItem(
                label="Cover A",
                locg_url=issue.locg_url,
                cover_image_url=issue.cover_image_url,
            )]
        cover_variants = cover_a + [
            CoverVariantItem(
                label=c.cover_label,
                locg_url=(
                    f"{issue.locg_url}?variant={c.locg_cover_id}"
                    if issue.locg_url and c.locg_cover_id else None
                ),
                cover_image_url=c.cover_image_url,
            )
            for c in issue.covers if c.cover_label
        ]
        tracked_artists = [
            ca.artist.name
            for cover in issue.covers
            for ca in cover.cover_artists
            if ca.artist and ca.artist.is_tracked
        ]
        rows.append(FocExportRow(
            series_name=issue.series.name if issue.series else "",
            series_url=issue.series.locg_url if issue.series else None,
            issue_number=issue.issue_number,
            foc_date=issue.foc_date,
            locg_url=issue.locg_url,
            cover_variants=cover_variants,
            has_tracked_artist=bool(tracked_artists),
            artist_names=list(set(tracked_artists)),
        ))

    data = [r.model_dump(mode="json") for r in rows]
    _write("foc-export.json", data)
    print(f"    {len(data)} FOC rows")


def export_reprints(db) -> None:
    alerts = (
        db.query(Notification)
        .join(Notification.issue)
        .join(Issue.series)
        .filter(
            Notification.type == "REPRINT_ALERT",
            Series.is_followed == True,
        )
        .order_by(Notification.reprint_date.asc().nullslast(), Issue.release_date.asc().nullslast())
        .all()
    )

    groups: dict = {}
    group_order: list = []
    for alert in alerts:
        issue = alert.issue
        key = (alert.issue_id, str(alert.reprint_date))
        if key not in groups:
            groups[key] = {"alert": alert, "issue": issue, "covers": []}
            group_order.append(key)
        label = alert.title.replace("Reprint announced: ", "")
        cover_image = alert.cover_image_url or (issue.cover_image_url if issue else None)
        groups[key]["covers"].append(CoverVariantItem(
            label=label,
            locg_url=issue.locg_url if issue else None,
            cover_image_url=cover_image,
        ))

    rows = []
    for key in group_order:
        g = groups[key]
        issue = g["issue"]
        alert = g["alert"]
        rows.append(FocExportRow(
            series_name=issue.series.name if issue and issue.series else "",
            issue_number=issue.issue_number if issue else None,
            foc_date=issue.foc_date if issue else None,
            release_date=issue.release_date if issue else None,
            reprint_date=alert.reprint_date,
            locg_url=issue.locg_url if issue else None,
            cover_variants=g["covers"],
            has_tracked_artist=False,
            artist_names=[],
        ))

    data = [r.model_dump(mode="json") for r in rows]
    _write("reprints.json", data)
    print(f"    {len(data)} reprint rows")


def export_notifications(db) -> None:
    last_sync = db.query(SyncLog).order_by(SyncLog.started_at.desc()).first()
    last_sync_at = last_sync.started_at if last_sync else None

    notifs = (
        db.query(Notification)
        .filter(Notification.type != "SYNC_ERROR")
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )

    rows = []
    new_count = 0
    for n in notifs:
        row = NotificationRead.model_validate(n).model_dump(mode="json")
        # Blue dot = created during the most recent sync only
        is_new = last_sync_at is not None and n.created_at >= last_sync_at
        row["is_read"] = not is_new
        if is_new:
            new_count += 1
        rows.append(row)

    _write("notifications.json", rows)
    print(f"    {len(rows)} notifications ({new_count} new from last sync)")


def export_sync_info(db) -> None:
    log = (
        db.query(SyncLog)
        .order_by(SyncLog.started_at.desc())
        .first()
    )
    if log:
        data = SyncLogRead.model_validate(log).model_dump(mode="json")
    else:
        # No syncs yet — write a placeholder
        data = {
            "id": 0,
            "job_name": "none",
            "status": "success",
            "records_fetched": 0,
            "records_inserted": 0,
            "error_message": None,
            "error_detail": None,
            "started_at": datetime.utcnow().isoformat(),
            "finished_at": None,
        }
    _write("sync-info.json", data)


def main() -> None:
    print("Exporting static data…")
    db = SessionLocal()
    try:
        export_tracked_artists(db)
        export_artist_alerts(db)
        export_upcoming_issues(db)
        export_foc(db)
        export_reprints(db)
        export_notifications(db)
        export_sync_info(db)
    finally:
        db.close()
    print("Done.")


if __name__ == "__main__":
    main()
