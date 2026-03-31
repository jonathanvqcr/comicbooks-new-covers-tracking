from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pathlib import Path
from typing import List

from backend.database import get_db
from backend.models import Report
from backend.schemas import ReportRead

router = APIRouter()

REPORTS_DIR = Path("reports")


@router.get("/reports", response_model=List[ReportRead])
def list_reports(db: Session = Depends(get_db)):
    return db.query(Report).order_by(Report.generated_at.desc()).all()


@router.get("/reports/{report_id}/download")
def download_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    pdf_path = REPORTS_DIR / report.filename
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=report.filename,
    )


@router.post("/reports/generate-now")
def generate_report_now(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from backend.jobs.generate_report import generate_weekly_report
    from backend.models import NotificationSettings
    settings = db.query(NotificationSettings).filter(NotificationSettings.id == 1).first()
    email_enabled = settings.email_enabled if settings else False
    background_tasks.add_task(generate_weekly_report, db, email_enabled)
    return {"message": "Report generation started", "job_id": "manual"}
