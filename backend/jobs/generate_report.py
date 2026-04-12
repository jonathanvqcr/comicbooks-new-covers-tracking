"""
Weekly PDF report generation job.
Uses WeasyPrint to render the Jinja2 HTML template -> PDF.
Saves to reports/ directory.
Optionally emails the PDF.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session
from weasyprint import HTML

from backend.config import settings
from backend.models import Artist, CoverArtist, Issue, IssueCover, Notification, Report, Series

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _BACKEND_DIR / "email" / "templates"
_REPORTS_DIR = _BACKEND_DIR.parent / "reports"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )


def _fmt(d: date | None) -> str:
    return d.strftime("%B %d, %Y") if d else "—"


def _build_foc_rows(db: Session) -> list[dict]:
    today = date.today()
    cutoff = today + relativedelta(months=3)

    issues = (
        db.query(Issue)
        .join(Series, Issue.series_id == Series.id)
        .filter(Issue.foc_date != None, Issue.foc_date >= today, Issue.foc_date <= cutoff)
        .order_by(Issue.foc_date)
        .all()
    )

    rows: list[dict] = []
    for issue in issues:
        cover_variants: list[str] = []
        artist_names: list[str] = []
        has_tracked = False

        for cover in issue.covers:
            if cover.cover_label:
                cover_variants.append(cover.cover_label)
            tracked = (
                db.query(Artist)
                .join(CoverArtist, CoverArtist.artist_id == Artist.id)
                .filter(
                    CoverArtist.issue_cover_id == cover.id,
                    Artist.is_tracked == True,  # noqa: E712
                )
                .all()
            )
            for artist in tracked:
                has_tracked = True
                if artist.name not in artist_names:
                    artist_names.append(artist.name)

        rows.append({
            "series_name": issue.series.name if issue.series else "Unknown",
            "issue_number": issue.issue_number,
            "foc_date": str(issue.foc_date) if issue.foc_date else "—",
            "cover_variants": cover_variants,
            "has_tracked_artist": has_tracked,
            "artist_names": artist_names,
        })

    return rows


def _build_notification_rows(db: Session) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=7)
    notifications = (
        db.query(Notification)
        .filter(Notification.created_at >= since)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return [
        {
            "id": n.id,
            "type": n.type,
            "title": n.title,
            "body": n.body,
            "created_at": n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else "",
        }
        for n in notifications
    ]


def generate_weekly_report(db: Session, send_email_enabled: bool = False) -> str:
    """
    Generate the weekly PDF report.

    1. Query upcoming FOC dates (next 8 weeks)
    2. Query recent notifications (last 7 days)
    3. Render weekly_report.html via Jinja2
    4. Convert HTML -> PDF via WeasyPrint
    5. Save to reports/{YYYY-MM-DD}_weekly_report.pdf
    6. Insert a Report row in the DB
    7. If send_email_enabled and report_email configured: email the PDF

    Returns the absolute path to the generated PDF.
    """
    today = date.today()
    period_end = today + relativedelta(months=3)

    foc_rows = _build_foc_rows(db)
    all_notifications = _build_notification_rows(db)
    reprints = [n for n in all_notifications if n["type"] == "REPRINT_ALERT"]
    artist_alerts = [n for n in all_notifications if n["type"] == "ARTIST_COVER_ALERT"]

    env = _jinja_env()
    template = env.get_template("weekly_report.html")
    html_content = template.render(
        report_date=_fmt(today),
        period_start=_fmt(today),
        period_end=_fmt(period_end),
        foc_rows=foc_rows,
        notifications=all_notifications,
        reprints=reprints,
        artist_alerts=artist_alerts,
    )

    pdf_bytes = HTML(string=html_content, base_url=str(_BACKEND_DIR)).write_pdf()

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{today.strftime('%Y-%m-%d')}_weekly_report.pdf"
    pdf_path = _REPORTS_DIR / filename
    pdf_path.write_bytes(pdf_bytes)
    logger.info("Weekly report saved: %s (%d bytes)", pdf_path, len(pdf_bytes))

    report_record = Report(
        filename=filename,
        generated_at=datetime.utcnow(),
        period_start=today,
        period_end=period_end,
    )
    db.add(report_record)
    db.commit()

    if send_email_enabled and settings.report_email:
        import asyncio
        from backend.email.sender import send_email

        email_template = env.get_template("report_email.html")
        email_html = email_template.render(
            report_date=_fmt(today),
            pdf_attached=True,
            foc_count=len(foc_rows),
            artist_alert_count=len(artist_alerts),
        )
        asyncio.run(
            send_email(
                to=settings.report_email,
                subject=f"Comic Book Weekly Report — {_fmt(today)}",
                html_body=email_html,
                attachment_path=pdf_path,
                attachment_filename=filename,
            )
        )
        logger.info("Weekly report emailed to %s", settings.report_email)

    return str(pdf_path)
