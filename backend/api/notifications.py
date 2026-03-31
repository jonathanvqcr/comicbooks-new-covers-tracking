from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from backend.database import get_db
from backend.models import Notification
from backend.schemas import NotificationRead, UnreadCountRead

router = APIRouter()


@router.get("/notifications", response_model=List[NotificationRead])
def get_notifications(unread_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(Notification)
    if unread_only:
        q = q.filter(Notification.is_read == False)
    return q.order_by(Notification.created_at.desc()).all()


@router.get("/notifications/unread-count", response_model=UnreadCountRead)
def get_unread_count(db: Session = Depends(get_db)):
    count = db.query(Notification).filter(Notification.is_read == False).count()
    return UnreadCountRead(count=count)


@router.post("/notifications/{notification_id}/read", response_model=NotificationRead)
def mark_read(notification_id: int, db: Session = Depends(get_db)):
    notif = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    db.refresh(notif)
    return notif


@router.post("/notifications/read-all")
def mark_all_read(db: Session = Depends(get_db)):
    marked = (
        db.query(Notification)
        .filter(Notification.is_read == False)
        .update({"is_read": True})
    )
    db.commit()
    return {"marked": marked}
