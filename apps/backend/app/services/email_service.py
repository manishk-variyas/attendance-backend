import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASS
        self.from_addr = settings.SMTP_FROM or settings.SMTP_USER
        self.enabled = settings.SMTP_ENABLED

    def send(self, to: List[str], subject: str, body_html: str) -> bool:
        if not self.enabled:
            logger.debug(f"Email not sent (disabled): {subject}")
            return False

        if not self.user or not self.password:
            logger.warning(f"SMTP not configured, skipping email: {subject}")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(to)
            msg["Subject"] = subject
            msg.attach(MIMEText(body_html, "html"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                server.login(self.user, self.password)
                server.send_message(msg)

            logger.info(f"Email sent: {subject} → {to}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email '{subject}': {e}")
            return False

    def send_leave_applied(self, to: str, applicant_name: str, start: str, end: str, reason: str) -> bool:
        subject = f"Leave Applied — {applicant_name} ({start} → {end})"
        body = _template("leave_applied", applicant_name=applicant_name, start=start, end=end, reason=reason)
        return self.send([to], subject, body)

    def send_leave_applied_confirmation(self, to: str, applicant_name: str, start: str, end: str, reason: str) -> bool:
        subject = f"Leave Submitted — {start} → {end}"
        body = _template("leave_applied_confirmation", applicant_name=applicant_name, start=start, end=end, reason=reason)
        return self.send([to], subject, body)

    def send_leave_approved(self, to: str, applicant_name: str, start: str, end: str) -> bool:
        subject = f"Leave Approved — {applicant_name} ({start} → {end})"
        body = _template("leave_approved", applicant_name=applicant_name, start=start, end=end)
        return self.send([to], subject, body)

    def send_leave_rejected(self, to: str, applicant_name: str, start: str, end: str) -> bool:
        subject = f"Leave Rejected — {applicant_name} ({start} → {end})"
        body = _template("leave_rejected", applicant_name=applicant_name, start=start, end=end)
        return self.send([to], subject, body)

    def send_cancel_requested(self, to: str, applicant_name: str, start: str, end: str, remark: str) -> bool:
        subject = f"Leave Cancellation Requested — {applicant_name} ({start} → {end})"
        body = _template("cancel_requested", applicant_name=applicant_name, start=start, end=end, remark=remark)
        return self.send([to], subject, body)

    def send_cancel_approved(self, to: str, applicant_name: str, start: str, end: str) -> bool:
        subject = f"Leave Cancellation Approved — {applicant_name} ({start} → {end})"
        body = _template("cancel_approved", applicant_name=applicant_name, start=start, end=end)
        return self.send([to], subject, body)

    def send_cancel_rejected(self, to: str, applicant_name: str, start: str, end: str, remark: str) -> bool:
        subject = f"Leave Cancellation Rejected — {applicant_name} ({start} → {end})"
        body = _template("cancel_rejected", applicant_name=applicant_name, start=start, end=end, remark=remark)
        return self.send([to], subject, body)


_EMAIL_TEMPLATES = {
    "leave_applied": """
        <div class="badge badge-info">New Leave Request</div>
        <p>Hello,</p>
        <p><strong>{applicant_name}</strong> has submitted a leave application that requires your review.</p>
        <table class="details">
            <tr><td>Applicant</td><td>{applicant_name}</td></tr>
            <tr><td>Start Date</td><td>{start}</td></tr>
            <tr><td>End Date</td><td>{end}</td></tr>
            <tr><td>Reason</td><td>{reason}</td></tr>
        </table>
        <p class="cta">Please login to the Attendance Portal to <strong>approve</strong> or <strong>reject</strong> this request.</p>
    """,
    "leave_applied_confirmation": """
        <div class="badge badge-info">Confirmation</div>
        <p>Hello {applicant_name},</p>
        <p>Your leave application has been submitted and sent to your manager for approval.</p>
        <table class="details">
            <tr><td>Start Date</td><td>{start}</td></tr>
            <tr><td>End Date</td><td>{end}</td></tr>
            <tr><td>Reason</td><td>{reason}</td></tr>
        </table>
        <p>You will receive an email notification once your manager reviews the request.</p>
    """,
    "leave_approved": """
        <div class="badge badge-success">Approved</div>
        <p>Hello {applicant_name},</p>
        <p>Your leave application has been <strong>approved</strong> by your manager.</p>
        <table class="details">
            <tr><td>Start Date</td><td>{start}</td></tr>
            <tr><td>End Date</td><td>{end}</td></tr>
        </table>
        <p>Your leave credits have been adjusted accordingly. Please plan your work handover before the leave period.</p>
    """,
    "leave_rejected": """
        <div class="badge badge-danger">Rejected</div>
        <p>Hello {applicant_name},</p>
        <p>Your leave application has been <strong>rejected</strong> by your manager.</p>
        <table class="details">
            <tr><td>Start Date</td><td>{start}</td></tr>
            <tr><td>End Date</td><td>{end}</td></tr>
        </table>
        <p>If you have questions about this decision, please contact your manager directly.</p>
    """,
    "cancel_requested": """
        <div class="badge badge-warning">Cancellation Request</div>
        <p>Hello,</p>
        <p><strong>{applicant_name}</strong> has requested to cancel an approved leave. Your approval is required to process this cancellation.</p>
        <table class="details">
            <tr><td>Applicant</td><td>{applicant_name}</td></tr>
            <tr><td>Leave Dates</td><td>{start} — {end}</td></tr>
            <tr><td>Cancellation Reason</td><td>{remark}</td></tr>
        </table>
        <p class="cta">Please login to the Attendance Portal to <strong>approve</strong> or <strong>reject</strong> this cancellation.</p>
    """,
    "cancel_approved": """
        <div class="badge badge-success">Cancellation Approved</div>
        <p>Hello {applicant_name},</p>
        <p>Your leave cancellation request has been <strong>approved</strong>. The leave has been successfully cancelled.</p>
        <table class="details">
            <tr><td>Leave Dates</td><td>{start} — {end}</td></tr>
        </table>
        <p>Your leave balance has been restored. You are expected to report to work as per your regular schedule on these dates.</p>
    """,
    "cancel_rejected": """
        <div class="badge badge-danger">Cancellation Rejected</div>
        <p>Hello {applicant_name},</p>
        <p>Your leave cancellation request has been <strong>rejected</strong> by your manager.</p>
        <table class="details">
            <tr><td>Leave Dates</td><td>{start} — {end}</td></tr>
            <tr><td>Manager's Remark</td><td>{remark}</td></tr>
        </table>
        <p>You may submit a new cancellation request with an updated reason (maximum 5 attempts).</p>
    """,
}


def _template(name: str, **kwargs) -> str:
    tpl = _EMAIL_TEMPLATES.get(name, "")
    result = tpl
    for k, v in kwargs.items():
        result = result.replace("{" + k + "}", str(v or "—"))
    result = result.replace("\n        ", "\n").strip()
    return f"""<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#172b4d;line-height:1.6">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:32px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08)">
    <tr>
        <td style="background:linear-gradient(135deg,#1a2332,#2d3f54);padding:28px 36px;text-align:center">
            <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px">Attendance Portal</h1>
            <p style="margin:4px 0 0;color:#8899aa;font-size:12px">Variyas Labs</p>
        </td>
    </tr>
    <tr>
        <td style="padding:32px 36px">
            <style>
                .badge {{display:inline-block;padding:6px 14px;border-radius:20px;font-size:12px;font-weight:700;letter-spacing:0.3px;text-transform:uppercase;margin-bottom:18px}}
                .badge-success {{background:#e3fcef;color:#006644}}
                .badge-danger {{background:#ffebe6;color:#bf2600}}
                .badge-warning {{background:#fffae6;color:#974f0c}}
                .badge-info {{background:#deebff;color:#0747a6}}
                .details {{width:100%;border-collapse:collapse;margin:18px 0;font-size:14px}}
                .details td {{padding:10px 14px;border-bottom:1px solid #ebeef1}}
                .details td:first-child {{color:#6b778c;font-weight:600;white-space:nowrap;width:160px}}
                .details td:last-child {{color:#172b4d}}
                p {{margin:0 0 14px;font-size:14px;color:#42526e}}
                .cta {{background:#f4f5f7;padding:16px 18px;border-radius:8px;text-align:center;font-size:13px;margin:18px 0 0}}
                .cta a {{color:#0052cc;text-decoration:none;font-weight:600}}
            </style>
            {result}
        </td>
    </tr>
    <tr>
        <td style="background:#f4f5f7;padding:20px 36px;text-align:center">
            <p style="margin:0;font-size:11px;color:#8993a4;line-height:1.8">
                This is an automated notification from the Attendance Portal.<br>
                Please do not reply to this email. For assistance, contact your administrator.
            </p>
            <p style="margin:10px 0 0;font-size:11px;color:#b3bac5">© Variyas Labs Attendance System</p>
        </td>
    </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


email_service = EmailService()
