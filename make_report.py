"""Build a polished HTML project report and (optionally) save it as a Gmail
draft via IMAP, with the dashboard screenshot embedded inline."""
import imaplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import formatdate

RECIPIENTS = ["ahmet.sungur@sundanismanlik.net", "esra.serin@sundanismanlik.net"]
SUBJECT = "Grants Monitoring System — Project Overview & Progress"
SCREENSHOT = "dashboard_screenshot.png"

STATS = [
    ("65", "Sources monitored"),
    ("173", "Deadlines tracked"),
]

FEATURES = [
    ("Automatic monitoring", "Scans 65 institutional websites every few hours and detects newly announced grant &amp; funding programs."),
    ("Detail enrichment", "Extracts each program&rsquo;s deadline, funding amount, eligibility and contact info from the source page."),
    ("Triage workflow", "Programs flow Inbox &rarr; Accepted / Rejected so the team can quickly curate what matters."),
    ("Smart filtering", "Filter by category, institution, release date and keyword; expired programs are hidden and results sort by nearest deadline."),
    ("Team email alerts", "A clean email with program name, institution, link and deadline &mdash; automatically and via a &ldquo;Send to team&rdquo; button."),
    ("Bilingual interface", "Full English / Turkish dashboard."),
]

CHALLENGES = [
    ("Result quality", "Early scans pulled in website menus, sidebars and press releases as if they were programs. We rebuilt the extraction logic so the inbox now holds genuine, open opportunities."),
    ("Turkish language", "Letter casing (&#304;/I/&#305;/i) and word suffixes caused both false matches and missed programs; Turkish-aware text handling fixed this."),
    ("Difficult websites", "Several government sites had broken certificates, bot-blocking, changing URLs or corrupted text &mdash; each was handled so the maximum number of sources scan reliably."),
    ("Duplicates", "The same program reappeared as &ldquo;new&rdquo; when its link changed slightly; normalization and a permanent archive now record each program once."),
    ("Deadlines", "Deadlines are written as free text; we built Turkish/English date parsing, populated 600+ programs in one pass, and now hide programs whose deadline has passed."),
]


def _section_title(text, color):
    return (
        f'<tr><td style="padding:26px 0 12px 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="width:5px;background:{color};border-radius:3px;">&nbsp;</td>'
        f'<td style="padding-left:12px;font:700 18px Arial,sans-serif;color:#101828;">{text}</td>'
        f'</tr></table></td></tr>'
    )


def build_report_html(img_src):
    # Stats strip
    stat_cells = ""
    col_w = int(100 / len(STATS)) if STATS else 100
    for value, label in STATS:
        stat_cells += (
            f'<td width="{col_w}%" align="center" style="padding:14px 6px;">'
            f'<div style="font:800 26px Arial,sans-serif;color:#2563eb;">{value}</div>'
            f'<div style="font:12px Arial,sans-serif;color:#667085;margin-top:2px;">{label}</div>'
            f'</td>'
        )

    feature_rows = ""
    for name, desc in FEATURES:
        feature_rows += (
            f'<tr><td style="padding:7px 0;vertical-align:top;width:26px;">'
            f'<div style="width:20px;height:20px;border-radius:50%;background:#eafaf1;color:#067647;'
            f'font:700 13px Arial,sans-serif;text-align:center;line-height:20px;">&#10003;</div></td>'
            f'<td style="padding:7px 0 7px 4px;font:14px/1.5 Arial,sans-serif;color:#344054;">'
            f'<b style="color:#101828;">{name}.</b> {desc}</td></tr>'
        )

    challenge_rows = ""
    for name, desc in CHALLENGES:
        challenge_rows += (
            f'<tr><td style="padding:7px 0;vertical-align:top;width:26px;">'
            f'<div style="width:20px;height:20px;border-radius:50%;background:#fff4e5;color:#b54708;'
            f'font:700 13px Arial,sans-serif;text-align:center;line-height:20px;">&#9888;</div></td>'
            f'<td style="padding:7px 0 7px 4px;font:14px/1.5 Arial,sans-serif;color:#344054;">'
            f'<b style="color:#101828;">{name}.</b> {desc}</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#eef2f6;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f6;padding:28px 0;">
   <tr><td align="center">
    <table role="presentation" width="660" cellpadding="0" cellspacing="0" style="width:660px;max-width:94%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 4px rgba(16,24,40,.08);">

      <!-- Header -->
      <tr><td style="background:#101828;padding:34px 36px;">
        <div style="font:800 24px Arial,sans-serif;color:#ffffff;letter-spacing:-.3px;">Grants Observatory</div>
        <div style="font:15px Arial,sans-serif;color:#84caff;margin-top:6px;">Project Overview &amp; Progress Report</div>
      </td></tr>

      <!-- Body -->
      <tr><td style="padding:30px 36px 8px 36px;">
        <p style="font:15px/1.6 Arial,sans-serif;color:#344054;margin:0 0 16px 0;">Dear Ahmet, Dear Esra,</p>
        <p style="font:15px/1.6 Arial,sans-serif;color:#344054;margin:0 0 8px 0;">
          Below is a summary of the Grants Monitoring System we have been developing &mdash; what it does,
          the challenges we worked through, and what has been completed. A live screenshot of the dashboard
          is included.</p>

        <!-- Stats -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
               style="margin:22px 0 6px 0;background:#f8fafc;border:1px solid #eaecf0;border-radius:12px;">
          <tr>{stat_cells}</tr>
        </table>

        <!-- Screenshot -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0 4px 0;">
          <tr><td style="padding:6px;background:#101828;border-radius:12px;">
            <img src="{img_src}" alt="Grants Observatory dashboard" width="100%"
                 style="display:block;width:100%;border-radius:8px;"></td></tr>
          <tr><td style="font:12px Arial,sans-serif;color:#98a2b3;text-align:center;padding:8px 0 0 0;">
            The live dashboard &mdash; sources, triage inbox, filters and team-email controls.</td></tr>
        </table>

        <!-- What it is -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          {_section_title("What it is", "#2563eb")}
          <tr><td style="font:14px/1.6 Arial,sans-serif;color:#344054;">
            An internal web application that automatically monitors <b>65 institutional websites</b> &mdash;
            ministries, development agencies, public banks, NGOs/foundations, embassies and EU/international
            sources &mdash; and surfaces newly announced grant and funding programs
            (&ldquo;hibe / destek programlar&#305;&rdquo;) in one place.</td></tr>

          {_section_title("What it does", "#067647")}
          <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{feature_rows}</table></td></tr>

          {_section_title("Challenges we worked through", "#b54708")}
          <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{challenge_rows}</table></td></tr>

          {_section_title("Current status", "#7a5af8")}
          <tr><td style="font:14px/1.6 Arial,sans-serif;color:#344054;padding-bottom:6px;">
            The system is operational: it scans 65 sources, currently tracks ~640 curated open programs,
            enriches details, filters by date/deadline and emails the team. Email delivery has been tested
            and verified. I would be glad to walk you through a live demo at your convenience.</td></tr>
        </table>

        <p style="font:15px/1.6 Arial,sans-serif;color:#344054;margin:22px 0 4px 0;">Best regards,</p>
        <p style="font:15px/1.5 Arial,sans-serif;color:#101828;margin:0 0 22px 0;">
          <b>Anis Nakib</b><br>
          <span style="font:13px Arial,sans-serif;color:#667085;">AI &amp; Automation Specialist</span></p>
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#f8fafc;border-top:1px solid #eaecf0;padding:16px 36px;
                 font:12px Arial,sans-serif;color:#98a2b3;">
        Generated from the Grants Monitoring System.</td></tr>
    </table>
   </td></tr>
  </table>
</body></html>"""


def create_draft(user, app_password):
    """Append the report as a draft to the account's Gmail Drafts folder."""
    with open(SCREENSHOT, "rb") as f:
        img_bytes = f.read()

    root = MIMEMultipart("related")
    root["Subject"] = SUBJECT
    root["From"] = user
    root["To"] = ", ".join(RECIPIENTS)
    root["Date"] = formatdate(localtime=True)

    root.attach(MIMEText(build_report_html("cid:dashboard"), "html", "utf-8"))
    img = MIMEImage(img_bytes, _subtype="png")
    img.add_header("Content-ID", "<dashboard>")
    img.add_header("Content-Disposition", "inline", filename="dashboard.png")
    root.attach(img)

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(user, app_password)
    M.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), root.as_bytes())
    M.logout()
    print(f"Draft created in {user} Drafts for {RECIPIENTS}")


if __name__ == "__main__":
    # Preview build (image referenced as a local file so a browser can render it).
    with open("report_preview.html", "w", encoding="utf-8") as f:
        f.write(build_report_html(SCREENSHOT))
    print("preview written to report_preview.html")
