from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from backend.database import engine, Base, SessionLocal
from backend.models import NotificationSettings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Ensure data and reports directories exist
    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    # Ensure default notification settings row exists
    db = SessionLocal()
    try:
        if not db.query(NotificationSettings).first():
            db.add(NotificationSettings(id=1))
            db.commit()
    finally:
        db.close()

    # Start background scheduler (sync + report jobs)
    from backend.scheduler import start_scheduler
    start_scheduler()

    yield

    from backend.scheduler import stop_scheduler
    stop_scheduler()


app = FastAPI(
    title="Comic Book Tracker",
    description="Track FOC dates, cover variants, and artist covers from League of Comic Geeks",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
from backend.api import issues, notifications, settings, reports, admin

app.include_router(issues.router, prefix="/api", tags=["issues"])
app.include_router(notifications.router, prefix="/api", tags=["notifications"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(admin.router, prefix="/api", tags=["admin"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
