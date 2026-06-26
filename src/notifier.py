from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def notify_report_ready(report_path: str, subject: str | None = None) -> None:
    report = Path(report_path)
    print(f"Report generated: {report_path}")

    smtp_host = _env("SMTP_HOST")
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    smtp_to = _env("SMTP_TO")
    if not all([smtp_host, smtp_user, smtp_password, smtp_to]):
        logging.info("Email settings are incomplete; skipping email delivery.")
        return

    smtp_port = int(_env("SMTP_PORT") or "587")
    smtp_from = _env("SMTP_FROM") or smtp_user
    recipients = [item.strip() for item in smtp_to.replace(";", ",").split(",") if item.strip()]
    if not recipients:
        logging.info("SMTP_TO is empty; skipping email delivery.")
        return

    content = report.read_text(encoding="utf-8")
    message = EmailMessage()
    message["From"] = smtp_from
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject or report.stem
    message.set_content(content)
    message.add_attachment(
        content.encode("utf-8"),
        maintype="text",
        subtype="markdown",
        filename=report.name,
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
    logging.info("Email report sent to %s", ", ".join(recipients))
