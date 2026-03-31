from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import date, timedelta
from typing import List

from backend.database import get_db
from backend.models import Issue, Series, IssueCover, Artist, CoverArtist, Notification
from backend.schemas import IssueRead, IssueCoverRead, FocExportRow, CoverVariantItem

router = APIRouter()


def _build_issue_read(issue: Issue, db: Session) -> IssueRead:
    covers = []
    for cover in issue.covers:
        artist_names = [
            ca.artist.name for ca in cover.cover_artists if ca.artist
        ]
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


@router.get("/issues/upcoming", response_model=List[IssueRead])
def get_upcoming_issues(db: Session = Depends(get_db)):
    cutoff = date.today() + timedelta(weeks=12)
    issues = (
        db.query(Issue)
        .join(Issue.series)
        .filter(
            Series.is_followed == True,
            or_(
                Issue.foc_date <= cutoff,
                Issue.release_date <= cutoff,
            ),
            or_(
                Issue.foc_date >= date.today(),
                Issue.release_date >= date.today(),
            ),
        )
        .order_by(Issue.foc_date.asc().nullslast())
        .all()
    )
    return [_build_issue_read(issue, db) for issue in issues]


@router.get("/issues/foc-export", response_model=List[FocExportRow])
def get_foc_export(db: Session = Depends(get_db)):
    cutoff = date.today() + timedelta(weeks=12)
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
        # Cover A is the main issue cover — never stored in issue_covers, lives on the issue itself
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
            issue_number=issue.issue_number,
            foc_date=issue.foc_date,
            locg_url=issue.locg_url,
            cover_variants=cover_variants,
            has_tracked_artist=bool(tracked_artists),
            artist_names=list(set(tracked_artists)),
        ))
    return rows


@router.get("/issues/reprints", response_model=List[FocExportRow])
def get_reprints(db: Session = Depends(get_db)):
    """One row per (issue, reprint_date) with all variant covers combined."""
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

    # Group by (issue_id, reprint_date) — collect all covers per group
    groups: dict = {}
    group_order: list = []
    for alert in alerts:
        issue = alert.issue
        key = (alert.issue_id, alert.reprint_date)
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
    return rows


@router.get("/series/{series_id}/issues", response_model=List[IssueRead])
def get_series_issues(series_id: int, db: Session = Depends(get_db)):
    series = db.query(Series).filter(Series.id == series_id).first()
    if not series:
        raise HTTPException(status_code=404, detail="Series not found")
    issues = (
        db.query(Issue)
        .filter(Issue.series_id == series_id)
        .order_by(Issue.issue_number.asc())
        .all()
    )
    return [_build_issue_read(issue, db) for issue in issues]


@router.get("/issues/{issue_id}", response_model=IssueRead)
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return _build_issue_read(issue, db)
