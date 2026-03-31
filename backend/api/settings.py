from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import NotificationSettings
from backend.schemas import NotificationSettingsRead, NotificationSettingsUpdate

router = APIRouter()


@router.get("/settings", response_model=NotificationSettingsRead)
def get_settings(db: Session = Depends(get_db)):
    row = db.query(NotificationSettings).filter(NotificationSettings.id == 1).first()
    if not row:
        row = NotificationSettings(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


@router.patch("/settings", response_model=NotificationSettingsRead)
def update_settings(payload: NotificationSettingsUpdate, db: Session = Depends(get_db)):
    row = db.query(NotificationSettings).filter(NotificationSettings.id == 1).first()
    if not row:
        row = NotificationSettings(id=1)
        db.add(row)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)

    db.commit()
    db.refresh(row)
    return row
