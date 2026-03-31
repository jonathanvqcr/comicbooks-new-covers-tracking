from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from backend.database import get_db
from backend.models import SyncLog
from backend.schemas import SyncNowResponse, SyncLogRead

router = APIRouter()


@router.post("/admin/sync-now", response_model=SyncNowResponse)
def sync_now():
    from backend.scheduler import trigger_sync_now
    job_id = trigger_sync_now()
    return SyncNowResponse(message="Sync triggered", job_id=job_id)


@router.get("/admin/sync-log", response_model=List[SyncLogRead])
def get_sync_log(db: Session = Depends(get_db)):
    return (
        db.query(SyncLog)
        .order_by(SyncLog.started_at.desc())
        .limit(50)
        .all()
    )
