"""
Email sender using aiosmtplib + Gmail SMTP.
Reads credentials from config.settings.
"""
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from backend.config import settings


async def send_email(
    to: str,
    subject: str,
    html_body: str,
    attachment_path: Path | None = None,
    attachment_filename: str | None = None,
) -> None:
    """Send an HTML email via Gmail SMTP. Attaches a file if provided."""
    if not settings.email_from or not settings.email_password:
        raise ValueError("Email credentials not configured in .env")

    msg = MIMEMultipart("mixed")
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    if attachment_path and attachment_path.exists():
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(
                f.read(), Name=attachment_filename or attachment_path.name
            )
            part["Content-Disposition"] = (
                f'attachment; filename="{attachment_filename or attachment_path.name}"'
            )
            msg.attach(part)

    await aiosmtplib.send(
        msg,
        hostname="smtp.gmail.com",
        port=587,
        start_tls=True,
        username=settings.email_from,
        password=settings.email_password,
    )
