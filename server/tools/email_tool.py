"""
tools/email_tool.py

Sends emails via SMTP with full TLS/SSL support.

Resend SMTP config (from .env):
  SMTP_HOST=smtp.resend.com
  SMTP_PORT=465
  SMTP_USERNAME=resend
  SMTP_PASSWORD=re_...
  SMTP_FROM_EMAIL=noreply@yourdomain.com
  SMTP_FROM_NAME=Maanis AI
  SMTP_USE_SSL=true      ← port 465 = direct SSL (smtplib.SMTP_SSL)
  SMTP_USE_TLS=false     ← port 587 = STARTTLS

AUTO-DETECT:
  Port 465 → SMTP_SSL (direct TLS)
  Port 587 → SMTP + STARTTLS
  Env SMTP_USE_SSL=true forces SSL regardless of port

Template placeholders: {{name}}, {{email}}, {{first_name}}
"""

import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class EmailResult:
    success: bool
    sent: int
    failed: int
    errors: List[Dict[str, str]]
    message: str


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _get_smtp_config() -> Dict[str, Any]:
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", "")
    from_name = os.getenv("SMTP_FROM_NAME", "System Notifications")

    # Determine connection mode
    # Port 465 always uses direct SSL. Port 587 uses STARTTLS.
    # SMTP_USE_SSL env can force SSL on any port.
    use_ssl_env = os.getenv("SMTP_USE_SSL", "").lower()
    use_tls_env = os.getenv("SMTP_USE_TLS", "").lower()

    if use_ssl_env == "true":
        mode = "ssl"
    elif use_tls_env == "true" and port != 465:
        mode = "starttls"
    elif port == 465:
        mode = "ssl"
    elif port == 587:
        mode = "starttls"
    else:
        mode = "plain"

    if not host:
        raise ValueError("SMTP_HOST environment variable is required")
    if not from_email:
        raise ValueError("SMTP_FROM_EMAIL environment variable is required")

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "mode": mode,  # "ssl" | "starttls" | "plain"
    }


def _validate_email(email: str) -> bool:
    return bool(email and EMAIL_REGEX.match(str(email).strip()))


def _personalize_content(content: str, recipient: Dict[str, Any]) -> str:
    result = content
    name = str(recipient.get("name", "")).strip()

    if not name:
        result = re.sub(r"Dear\s*\{\{name\}\}", "Dear Employee", result, flags=re.IGNORECASE)
        result = result.replace("{{name}}", "Valued Team Member")
    else:
        result = result.replace("{{name}}", name)
        result = result.replace("{{first_name}}", name.split()[0])

    result = result.replace("{{email}}", str(recipient.get("email", "")))
    return result


def _send_single_email(
    smtp_config: Dict[str, Any],
    recipient: Dict[str, Any],
    subject: str,
    body: str,
    is_html: bool = False,
) -> Optional[str]:
    """
    Send one email. Returns None on success, error string on failure.

    Handles three connection modes:
      ssl      → smtplib.SMTP_SSL (port 465, Resend default)
      starttls → smtplib.SMTP + server.starttls() (port 587)
      plain    → smtplib.SMTP (no encryption, dev only)
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{smtp_config['from_name']} <{smtp_config['from_email']}>"
        recipient_email = str(recipient["email"]).strip()
        recipient_name = str(recipient.get("name", "")).strip()
        msg["To"] = f"{recipient_name} <{recipient_email}>" if recipient_name else recipient_email

        content_type = "html" if is_html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        host = smtp_config["host"]
        port = smtp_config["port"]
        mode = smtp_config["mode"]
        username = smtp_config["username"]
        password = smtp_config["password"]
        ctx = ssl.create_default_context()

        if mode == "ssl":
            # Direct SSL — Resend on port 465
            with smtplib.SMTP_SSL(host, port, context=ctx) as server:
                if username and password:
                    server.login(username, password)
                server.sendmail(smtp_config["from_email"], recipient_email, msg.as_string())

        elif mode == "starttls":
            with smtplib.SMTP(host, port) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                if username and password:
                    server.login(username, password)
                server.sendmail(smtp_config["from_email"], recipient_email, msg.as_string())

        else:
            with smtplib.SMTP(host, port) as server:
                if username and password:
                    server.login(username, password)
                server.sendmail(smtp_config["from_email"], recipient_email, msg.as_string())

        return None  # success

    except smtplib.SMTPAuthenticationError:
        return "SMTP authentication failed — check SMTP_USERNAME and SMTP_PASSWORD"
    except smtplib.SMTPRecipientsRefused:
        return f"Recipient address rejected: {recipient_email}"
    except smtplib.SMTPException as e:
        return f"SMTP error: {e}"
    except Exception as e:
        return f"Failed to send: {e}"


def send_email(
    workspace_id: str,
    recipients: List[Dict[str, Any]],
    subject: str,
    body: str,
    is_html: bool = False,
    emit_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Send emails to a list of recipients with {{name}} personalization.

    Args:
        workspace_id: Workspace ID (for logging/isolation)
        recipients: [{"email": "...", "name": "..."}]
        subject: Email subject (can use {{name}})
        body: Email body (can use {{name}}, {{email}}, {{first_name}})
        is_html: True if body is HTML
        emit_callback: Optional progress callback(event_type, message)

    Returns:
        { success, sent, failed, errors, message }
    """
    if not recipients:
        return {"success": False, "sent": 0, "failed": 0, "errors": [{"error": "No recipients"}], "message": "No recipients provided."}

    if not subject or not body:
        return {"success": False, "sent": 0, "failed": 0, "errors": [{"error": "Missing subject or body"}], "message": "Subject and body required."}

    # Validate recipients
    valid, invalid = [], []
    for r in recipients:
        if isinstance(r, dict) and _validate_email(r.get("email", "")):
            valid.append(r)
        else:
            email = r.get("email", str(r)) if isinstance(r, dict) else str(r)
            invalid.append({"email": email, "error": "Invalid email format"})

    if not valid:
        return {"success": False, "sent": 0, "failed": len(invalid), "errors": invalid, "message": "No valid email addresses found."}

    try:
        smtp_config = _get_smtp_config()
    except ValueError as e:
        return {"success": False, "sent": 0, "failed": len(valid), "errors": [{"error": str(e)}], "message": str(e)}

    if emit_callback:
        emit_callback("email_progress", f"Sending to {len(valid)} recipient(s) via {smtp_config['mode'].upper()}...")

    sent_count = 0
    errors = list(invalid)

    for i, recipient in enumerate(valid):
        personalized_subject = _personalize_content(subject, recipient)
        personalized_body = _personalize_content(body, recipient)

        error = _send_single_email(smtp_config, recipient, personalized_subject, personalized_body, is_html)
        if error:
            errors.append({"email": recipient["email"], "error": error})
            print(f"DEBUG [email_tool]: Failed to send to {recipient['email']}: {error}")
        else:
            sent_count += 1
            print(f"DEBUG [email_tool]: Sent to {recipient['email']} ✓")

        if emit_callback and (i + 1) % 10 == 0:
            emit_callback("email_progress", f"Sent {sent_count}/{len(valid)} emails...")

    failed = len(recipients) - sent_count

    if sent_count == len(recipients):
        message = f"Successfully sent {sent_count} email(s)."
    elif sent_count > 0:
        message = f"Sent {sent_count} email(s). {failed} failed."
    else:
        message = f"Failed to send all {failed} email(s). Check SMTP configuration."

    return {
        "success": sent_count > 0,
        "sent": sent_count,
        "failed": failed,
        "errors": errors,
        "message": message,
    }


def preview_email(
    workspace_id: str,
    recipients: List[Dict[str, Any]],
    subject: str,
    body: str,
) -> Dict[str, Any]:
    if not recipients:
        return {"success": False, "previews": [], "message": "No recipients"}
    previews = []
    for r in recipients[:3]:
        previews.append({
            "recipient": r,
            "subject": _personalize_content(subject, r),
            "body_preview": _personalize_content(body, r)[:500],
        })
    return {
        "success": True,
        "total_recipients": len(recipients),
        "previews": previews,
        "message": f"Preview of {len(previews)}/{len(recipients)} emails",
    }


def generate_email_template(template_type: str, **kwargs) -> Dict[str, str]:
    """Static template library (used as fallback when LLM unavailable)."""
    templates = {
        "termination": {
            "subject": "Important Notice Regarding Your Employment",
            "body": "Dear {{name}},\n\nWe regret to inform you that your employment with our company will be terminated effective {effective_date}.\n\nPlease contact HR at {hr_contact} if you have any questions.\n\nSincerely,\nHuman Resources",
        },
        "promotion": {
            "subject": "Congratulations on Your Promotion!",
            "body": "Dear {{name}},\n\nWe are pleased to inform you of your promotion to {new_position}!\n\nThis is effective {effective_date} and reflects your outstanding contributions.\n\nCongratulations!\n\nHuman Resources",
        },
        "announcement": {
            "subject": "{announcement_title}",
            "body": "Dear {{name}},\n\n{announcement_body}\n\nIf you have any questions, please reach out.\n\nBest regards,\n{sender_name}",
        },
    }

    template = templates.get(template_type, {"subject": "", "body": "Dear {{name}},\n\n{email_content}\n\nBest regards"})
    subject = template["subject"]
    body = template["body"]

    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        subject = subject.replace(placeholder, str(value))
        body = body.replace(placeholder, str(value))

    return {"subject": subject, "body": body}
