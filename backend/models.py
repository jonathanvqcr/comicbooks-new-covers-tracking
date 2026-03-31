from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, Text,
    ForeignKey, UniqueConstraint, func
)
from sqlalchemy.orm import relationship
from backend.database import Base


class Series(Base):
    __tablename__ = "series"

    id = Column(Integer, primary_key=True)
    locg_series_id = Column(String, unique=True, nullable=True)
    name = Column(String, nullable=False)
    publisher = Column(String, nullable=True)
    slug = Column(String, nullable=True)
    locg_url = Column(String, nullable=True)
    priority = Column(String, default="regular")  # regular | occasional | coming_soon
    is_followed = Column(Boolean, default=True)
    cover_image_url = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    issues = relationship("Issue", back_populates="series", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="series")


class Issue(Base):
    __tablename__ = "issues"

    id = Column(Integer, primary_key=True)
    locg_issue_id = Column(String, unique=True, nullable=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=False)
    issue_number = Column(String, nullable=True)
    title = Column(String, nullable=True)
    release_date = Column(Date, nullable=True)
    foc_date = Column(Date, nullable=True)
    is_reprint = Column(Boolean, default=False)
    reprint_of_id = Column(Integer, ForeignKey("issues.id"), nullable=True)
    cover_image_url = Column(String, nullable=True)
    locg_url = Column(String, nullable=True)
    alerted_foc = Column(Boolean, default=False)
    alerted_release = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    series = relationship("Series", back_populates="issues")
    covers = relationship("IssueCover", back_populates="issue", cascade="all, delete-orphan")
    reprint_of = relationship("Issue", remote_side="Issue.id", foreign_keys=[reprint_of_id])
    notifications = relationship("Notification", back_populates="issue")


class IssueCover(Base):
    __tablename__ = "issue_covers"

    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    locg_cover_id = Column(String, unique=True, nullable=True)
    cover_label = Column(String, nullable=True)  # "Cover A", "1:10 Incentive", "Virgin", etc.
    cover_image_url = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    issue = relationship("Issue", back_populates="covers")
    cover_artists = relationship("CoverArtist", back_populates="cover", cascade="all, delete-orphan")


class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True)
    locg_creator_id = Column(String, unique=True, nullable=True)
    name = Column(String, nullable=False)
    locg_url = Column(String, nullable=True)
    is_tracked = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    cover_credits = relationship("CoverArtist", back_populates="artist")


class CoverArtist(Base):
    __tablename__ = "cover_artists"
    __table_args__ = (UniqueConstraint("issue_cover_id", "artist_id"),)

    id = Column(Integer, primary_key=True)
    issue_cover_id = Column(Integer, ForeignKey("issue_covers.id"), nullable=False)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=False)

    cover = relationship("IssueCover", back_populates="cover_artists")
    artist = relationship("Artist", back_populates="cover_credits")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)  # FOC_ALERT | RELEASE_ALERT | REPRINT_ALERT | ARTIST_COVER_ALERT | SYNC_ERROR
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    cover_image_url = Column(String, nullable=True)  # reprint-specific cover for REPRINT_ALERT
    reprint_date = Column(Date, nullable=True)         # release date of this specific printing
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    issue = relationship("Issue", back_populates="notifications")
    series = relationship("Series", back_populates="notifications")


class NotificationSettings(Base):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True, default=1)
    foc_alert_days = Column(Integer, default=14)
    email_enabled = Column(Boolean, default=False)
    email_address = Column(String, nullable=True)
    report_email = Column(String, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True)
    job_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # success | error | partial
    records_fetched = Column(Integer, default=0)
    records_inserted = Column(Integer, default=0)
    error_message = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    generated_at = Column(DateTime, server_default=func.now())
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
