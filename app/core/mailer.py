"""
Mailer — sends the emails the app needs (password reset, verification).

MAIL_MODE (read on every call):
  console  the message is written to the log — development only: the log then
           contains working reset links, so never use it for real users
  smtp     sent through SMTP_HOST / SMTP_PORT (STARTTLS by default)
  memory   kept in ``outbox`` (tests)

Sending is best effort: a failure is logged and never reaches the caller, so a
request cannot reveal whether an address has an account by failing or being slow.
"""

import smtplib
from email.message import EmailMessage
from typing import Dict, List

from app import config
from app.core.logger import get_logger

logger = get_logger(__name__)

outbox: List[Dict[str, str]] = []   # filled in MAIL_MODE=memory


def mask_email(address: str) -> str:
    """'priya@example.com' -> 'p***@example.com' (for logs)."""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def send_mail(to: str, subject: str, body: str) -> None:
    """Send one plain-text email according to MAIL_MODE; never raises."""
    mode = config.MAIL_MODE
    try:
        if mode == "memory":
            outbox.append({"to": to, "subject": subject, "body": body})
        elif mode == "smtp":
            _send_smtp(to, subject, body)
        else:
            logger.info("MAIL (console mode, development only) to %s: %s\n%s", mask_email(to), subject, body)
    except Exception:
        logger.exception("Could not send email to %s", mask_email(to))


def _send_smtp(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        raise RuntimeError("MAIL_MODE=smtp but SMTP_HOST is not set")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = config.MAIL_FROM, to, subject
    message.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
        if config.SMTP_STARTTLS:
            server.starttls()
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(message)


def reset_email(name: str, link: str) -> Dict[str, str]:
    return {
        "subject": "Reset your Digital Twin password",
        "body": (
            f"Hello {name},\n\n"
            f"Someone asked to reset the password for this account. If it was you, open the link "
            f"below within {config.RESET_TOKEN_MINUTES} minutes:\n\n{link}\n\n"
            "If it was not you, ignore this email: your password has not changed.\n"
        ),
    }


def verify_email_message(name: str, link: str) -> Dict[str, str]:
    return {
        "subject": "Confirm your email for Digital Twin",
        "body": (
            f"Hello {name},\n\n"
            f"Confirm that this is your email address by opening the link below "
            f"(valid for {config.VERIFY_TOKEN_HOURS} hours):\n\n{link}\n\n"
            "If you did not create an account, ignore this email.\n"
        ),
    }
