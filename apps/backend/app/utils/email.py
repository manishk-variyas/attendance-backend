import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not settings.SMTP_ENABLED:
        logger.warning("SMTP is disabled — email not sent to %s", to_email)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.send_message(msg)
        logger.info("Email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


async def send_password_reset_email(to_email: str, reset_link: str):
    loop = __import__("asyncio").get_running_loop()
    subject = "Reset Your Password — Attendance App"
    html = f"""\
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
  <h2>Password Reset Request</h2>
  <p>You requested to reset your password. Click the link below to set a new password:</p>
  <p>
    <a href="{reset_link}" style="background:#2563eb;color:#fff;padding:10px 20px;
       border-radius:6px;text-decoration:none;font-weight:bold;">
      Reset Password
    </a>
  </p>
  <p>This link expires in {settings.RESET_TOKEN_EXPIRE_MINUTES} minutes.</p>
  <p>If you didn't request this, you can ignore this email.</p>
  <hr>
  <p style="color:#888;font-size:12px;">Attendance App</p>
</body>
</html>"""
    return await loop.run_in_executor(None, _send_email, to_email, subject, html)
