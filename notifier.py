import base64
import html as _html
import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

import config

logger = logging.getLogger(__name__)

SENDGRID_ENDPOINT = "https://api.sendgrid.com/v3/mail/send"


def _deadline_cell(program):
    """Return (text, bg_color, fg_color) describing a program's deadline."""
    dd = (program.get("deadline_date") or "").strip()
    raw = (program.get("deadline") or "").strip()
    if dd:
        try:
            d = datetime.strptime(dd, "%Y-%m-%d").date()
            days = (d - datetime.now().date()).days
            if days < 0:
                return (f"{dd} — süresi doldu", "#fdecea", "#b42318")
            if days == 0:
                return (f"{dd} — bugün son gün", "#fff4e5", "#b54708")
            if days <= 7:
                return (f"{dd} — {days} gün kaldı", "#fff4e5", "#b54708")
            return (f"{dd} — {days} gün kaldı", "#eafaf1", "#067647")
        except ValueError:
            pass
    if raw:
        return (raw, "#eef2f6", "#475467")
    return ("Belirtilmemiş", "#eef2f6", "#98a2b3")


def build_programs_email(programs, heading="Hibe Programları / Grant Programs"):
    """Build a polished, email-client-safe HTML digest of programs.
    Each card shows program name, institution, deadline and a link."""
    cards = ""
    for p in programs:
        title = _html.escape(p.get("title") or "")
        institution = _html.escape(p.get("site_name") or "")
        category = _html.escape(p.get("site_category") or "")
        url = _html.escape(p.get("url") or "#", quote=True)
        funding = _html.escape((p.get("funding_amount") or "").strip())
        dl_text, dl_bg, dl_fg = _deadline_cell(p)
        dl_text = _html.escape(dl_text)

        funding_row = ""
        if funding:
            funding_row = (
                f'<tr><td style="padding:2px 0;font:13px Arial,sans-serif;color:#475467;">'
                f'<span style="color:#98a2b3;">Bütçe:</span> {funding}</td></tr>'
            )

        cards += f"""
        <tr>
          <td style="padding:0 0 16px 0;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #e4e7ec;border-radius:12px;border-collapse:separate;">
              <tr><td style="padding:18px 20px;">
                <div style="font:600 12px Arial,sans-serif;color:#3538cd;background:#eef4ff;
                            display:inline-block;padding:3px 10px;border-radius:999px;margin-bottom:10px;">{category}</div>
                <div style="font:700 17px/1.35 Arial,sans-serif;color:#101828;margin-bottom:6px;">{title}</div>
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                  <tr><td style="padding:2px 0;font:13px Arial,sans-serif;color:#475467;">
                      <span style="color:#98a2b3;">Kurum:</span> {institution}</td></tr>
                  {funding_row}
                  <tr><td style="padding:8px 0 0 0;">
                      <span style="font:600 13px Arial,sans-serif;color:{dl_fg};background:{dl_bg};
                                   padding:5px 12px;border-radius:8px;display:inline-block;">
                        ⏳ Son başvuru: {dl_text}</span></td></tr>
                </table>
                <div style="margin-top:16px;">
                  <a href="{url}" style="font:600 14px Arial,sans-serif;color:#ffffff;background:#2563eb;
                     text-decoration:none;padding:10px 20px;border-radius:8px;display:inline-block;">
                     Programı Görüntüle →</a>
                </div>
              </td></tr>
            </table>
          </td>
        </tr>"""

    heading = _html.escape(heading)
    count = len(programs)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f2f4f7;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:640px;max-width:94%;">
        <tr><td style="background:#101828;border-radius:14px 14px 0 0;padding:26px 28px;">
          <div style="font:700 20px Arial,sans-serif;color:#ffffff;">Grants Observatory</div>
          <div style="font:14px Arial,sans-serif;color:#98a2b3;margin-top:4px;">{heading}</div>
          <div style="font:600 13px Arial,sans-serif;color:#84caff;margin-top:10px;">{count} program</div>
        </td></tr>
        <tr><td style="background:#ffffff;padding:24px 28px 8px 28px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cards}</table>
        </td></tr>
        <tr><td style="background:#ffffff;border-radius:0 0 14px 14px;padding:14px 28px 26px 28px;
                   font:12px Arial,sans-serif;color:#98a2b3;border-top:1px solid #f2f4f7;">
          Bu e-posta Grants Monitoring System tarafından gönderilmiştir.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def build_grants_html(grants_list):
    """Render the new-grants digest as an HTML email body."""
    html_rows = ""
    for g in grants_list:
        keywords = ", ".join(g.get("keywords", []))
        title = g.get("title", "")
        url = g.get("url", "#")
        html_rows += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd;">{title}</td>
            <td style="padding:8px;border:1px solid #ddd;"><a href="{url}">Link</a></td>
            <td style="padding:8px;border:1px solid #ddd;">{keywords}</td>
        </tr>"""
    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;">
        <h2>New R&D Grants/Programs Found</h2>
        <p>{len(grants_list)} new item(s) detected:</p>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#2563eb;color:white;">
                <th style="padding:8px;border:1px solid #ddd;">Title</th>
                <th style="padding:8px;border:1px solid #ddd;">Link</th>
                <th style="padding:8px;border:1px solid #ddd;">Keywords</th>
            </tr>
            {html_rows}
        </table>
        <p style="color:#666;margin-top:20px;">— Grants Monitoring System</p>
    </body>
    </html>"""


def send_desktop_notification(title, message):
    if not config.DESKTOP_NOTIFICATIONS_ENABLED:
        return
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message[:256],
            app_name="Grants Monitor",
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Desktop notification failed: {e}")


def send_email_html(subject, html, recipients=None):
    """Send an HTML email to recipients via the configured provider.
    Returns (ok: bool, error: str|None). Used by alerts and the test endpoint."""
    recipients = recipients or config.EMAIL_RECIPIENTS
    if not recipients:
        return False, "No recipients configured"
    if not config.EMAIL_SENDER:
        return False, "No sender address configured"

    provider = (config.EMAIL_PROVIDER or "sendgrid").lower()
    if provider == "sendgrid":
        return _send_via_sendgrid(subject, html, recipients)
    if provider == "gmail_api":
        return _send_via_gmail_api(subject, html, recipients)
    return _send_via_smtp(subject, html, recipients)


def _send_via_gmail_api(subject, html, recipients):
    """Send via the Gmail API using the OAuth token from gmail_auth.py. No password."""
    if not os.path.exists(config.GMAIL_TOKEN_FILE):
        return False, "Gmail not authorized yet — run: python gmail_auth.py"
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_FILE, config.GMAIL_SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(config.GMAIL_TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            else:
                return False, "Gmail token invalid — re-run gmail_auth.py"

        from_name = config.EMAIL_FROM_NAME or ""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{config.EMAIL_SENDER}>" if from_name else config.EMAIL_SENDER
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html", "utf-8"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        logger.info(f"Gmail API email sent to {len(recipients)} recipient(s).")
        return True, None
    except Exception as e:
        logger.error(f"Gmail API send failed: {e}")
        return False, str(e)


def _send_via_sendgrid(subject, html, recipients):
    if not config.SENDGRID_API_KEY:
        return False, "SendGrid API key not set"
    try:
        # One personalization per recipient => each gets an individual email.
        payload = {
            "personalizations": [{"to": [{"email": r}]} for r in recipients],
            "from": {"email": config.EMAIL_SENDER, "name": config.EMAIL_FROM_NAME},
            "subject": subject,
            "content": [{"type": "text/html", "value": html}],
        }
        resp = requests.post(
            SENDGRID_ENDPOINT,
            headers={
                "Authorization": f"Bearer {config.SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 202):
            logger.info(f"SendGrid email sent to {len(recipients)} recipient(s).")
            return True, None
        error = f"SendGrid HTTP {resp.status_code}: {resp.text[:300]}"
        logger.error(error)
        return False, error
    except Exception as e:
        logger.error(f"SendGrid send failed: {e}")
        return False, str(e)


def _send_via_smtp(subject, html, recipients):
    if not config.EMAIL_SMTP_SERVER:
        return False, "SMTP server not set"
    if not config.EMAIL_PASSWORD:
        return False, "SMTP password not set"
    try:
        from_name = config.EMAIL_FROM_NAME or ""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{config.EMAIL_SENDER}>" if from_name else config.EMAIL_SENDER
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html, "html", "utf-8"))

        port = int(config.EMAIL_SMTP_PORT)
        # Port 465 = implicit SSL; 587 (and others) = plain + STARTTLS.
        if port == 465:
            server = smtplib.SMTP_SSL(config.EMAIL_SMTP_SERVER, port, timeout=30)
        else:
            server = smtplib.SMTP(config.EMAIL_SMTP_SERVER, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        try:
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, recipients, msg.as_string())
        finally:
            server.quit()
        logger.info(f"SMTP email sent to {len(recipients)} recipient(s) via {config.EMAIL_SMTP_SERVER}:{port}")
        return True, None
    except Exception as e:
        logger.error(f"SMTP send failed: {e}")
        return False, str(e)


def send_email_alert(subject, grants_list):
    if not config.EMAIL_ENABLED or not config.EMAIL_RECIPIENTS:
        return
    html = build_grants_html(grants_list)
    send_email_html(subject, html)


def notify_new_grants(scan_result):
    """Alert on freshly-announced programs (scan_result['new_grants'] is already
    filtered to fresh announcements by the scanner)."""
    import database

    new_grants = scan_result.get("new_grants", [])
    if not new_grants:
        return

    count = len(new_grants)

    # Desktop notification
    titles = [g["title"][:60] for g in new_grants[:3]]
    summary = "\n".join(titles)
    if count > 3:
        summary += f"\n...and {count - 3} more"
    send_desktop_notification(f"{count} program newly announced", summary)

    # Email alert — use the polished template with full program data
    # (program name, institution, link, deadline).
    if not config.EMAIL_ENABLED or not config.EMAIL_RECIPIENTS:
        return
    rows = [database.get_grant_by_id(g["id"]) for g in new_grants if g.get("id")]
    rows = [r for r in rows if r]
    if not rows:
        return
    html = build_programs_email(rows, heading="Bugün Duyurulan Yeni Programlar / Newly Announced Today")
    send_email_html(f"[Grants] {count} yeni program duyuruldu", html)
