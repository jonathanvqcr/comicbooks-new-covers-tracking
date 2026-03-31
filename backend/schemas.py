from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional, List


# --- Series ---

class SeriesBase(BaseModel):
    name: str
    locg_url: Optional[str] = None
    priority: str = "regular"
    is_followed: bool = True
    cover_image_url: Optional[str] = None

class SeriesRead(SeriesBase):
    id: int
    locg_series_id: Optional[str] = None
    publisher: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Issue Covers ---

class IssueCoverRead(BaseModel):
    id: int
    cover_label: Optional[str] = None
    cover_image_url: Optional[str] = None
    artist_names: List[str] = []
    locg_url: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Issues ---

class IssueRead(BaseModel):
    id: int
    locg_issue_id: Optional[str] = None
    series_id: int
    series_name: str
    issue_number: Optional[str] = None
    title: Optional[str] = None
    release_date: Optional[date] = None
    foc_date: Optional[date] = None
    is_reprint: bool = False
    cover_image_url: Optional[str] = None
    locg_url: Optional[str] = None
    covers: List[IssueCoverRead] = []
    has_tracked_artist: bool = False

    model_config = {"from_attributes": True}


class CoverVariantItem(BaseModel):
    label: str
    locg_url: Optional[str] = None
    cover_image_url: Optional[str] = None


class FocExportRow(BaseModel):
    series_name: str
    issue_number: Optional[str]
    foc_date: Optional[date]
    release_date: Optional[date] = None
    reprint_date: Optional[date] = None
    locg_url: Optional[str] = None
    cover_variants: List[CoverVariantItem]
    has_tracked_artist: bool
    artist_names: List[str]


# --- Artists ---

class ArtistRead(BaseModel):
    id: int
    name: str
    locg_url: Optional[str] = None
    locg_creator_id: Optional[str] = None
    is_tracked: bool

    model_config = {"from_attributes": True}


# --- Notifications ---

class NotificationRead(BaseModel):
    id: int
    type: str
    title: str
    body: Optional[str] = None
    issue_id: Optional[int] = None
    series_id: Optional[int] = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}

class UnreadCountRead(BaseModel):
    count: int


# --- Settings ---

class NotificationSettingsRead(BaseModel):
    id: int
    foc_alert_days: int
    email_enabled: bool
    email_address: Optional[str] = None
    report_email: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}

class NotificationSettingsUpdate(BaseModel):
    foc_alert_days: Optional[int] = None
    email_enabled: Optional[bool] = None
    email_address: Optional[str] = None
    report_email: Optional[str] = None


# --- Sync Log ---

class SyncLogRead(BaseModel):
    id: int
    job_name: str
    status: str
    records_fetched: int
    records_inserted: int
    error_message: Optional[str] = None
    error_detail: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# --- Reports ---

class ReportRead(BaseModel):
    id: int
    filename: str
    generated_at: datetime
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    model_config = {"from_attributes": True}


# --- Admin ---

class SyncNowResponse(BaseModel):
    message: str
    job_id: str
