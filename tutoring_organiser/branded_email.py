"""The Croft Tutoring lesson invitation: the designed HTML email (plum header, date/time/length tiles,
Join on Google Meet button, signature, footer) plus a plain-text version for mail apps that don't show HTML.

The HTML is written the way email needs it: tables and inline styles only, because Gmail and Outlook ignore
most stylesheet rules. Images travel inside the email (cid: attachments), so nothing loads from the web.
Fonts: Apple Mail and iPhone show Roboto Slab and Inter; Gmail and Outlook fall back to Georgia and Arial.
"""
import html

from .config import BUNDLE_DIR
from .helpers import fmt_clock_time, fmt_duration
from .invites import lesson_date_phrase

EMAIL_ASSETS = BUNDLE_DIR / "assets" / "email"
HEADER_CID = "croft-header@crofttutoring"
ICON_CID = "croft-icon@crofttutoring"
HEADER_PATH = EMAIL_ASSETS / "header.png"
ICON_PATH = EMAIL_ASSETS / "signature-icon.png"

SIGNATURE_NAME = "Harrison Croft"
SIGNATURE_LINE = "Croft Tutoring · Maths & Physics · 027 533 3981"
PREP_LINE = "Have your calculator and any questions from class ready. See you then."
FOOTER_LINE = "Need to move this lesson? Reply to this email and we'll find another time."

PLUM, DEEP, PAPER, BLUSH_RULE, INK, MUTED, CANVAS = (
    "#7A2E4F", "#4A1A30", "#F7F3F0", "#E8DFE2", "#2B1E25", "#6F6067", "#EEE8E4")
SLAB = "'Roboto Slab', Georgia, 'Times New Roman', serif"
SANS = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"


def inline_images():
    """(path, content_id) pairs to attach with the HTML part, in the shape gmail_send expects."""
    return [(str(HEADER_PATH), HEADER_CID), (str(ICON_PATH), ICON_CID)]


def assets_available():
    return HEADER_PATH.is_file() and ICON_PATH.is_file()


def _first_name(name):
    return (name.split() or [name])[0] if name else ""


def _date_text(start):
    phrase = lesson_date_phrase(start)
    return "Today" if phrase == "today" else phrase


def render_plain(name, start, duration, meet_url):
    """Plain-text version, same words as the designed email."""
    link = meet_url or "[Meet link pending]"
    return (f"Hi {_first_name(name)},\n\n"
            f"Your lesson is booked.\n\n"
            f"Date: {_date_text(start)}\n"
            f"Time: {fmt_clock_time(start)}\n"
            f"Length: {fmt_duration(duration)}\n\n"
            f"Join on Google Meet:\n{link}\n\n"
            f"{PREP_LINE}\n\n"
            f"{SIGNATURE_NAME}\n{SIGNATURE_LINE}\n\n"
            f"{FOOTER_LINE}")


def _tile(label, value):
    return (f'<td class="ct-tile" valign="top" width="33%" style="padding:0 8px 0 0;">'
            f'<div style="font-family:{SANS};font-size:12px;font-weight:600;letter-spacing:1.2px;'
            f'text-transform:uppercase;color:{MUTED};line-height:15px;">{html.escape(label)}</div>'
            f'<div style="font-family:{SANS};font-size:17px;font-weight:600;color:{INK};line-height:21px;'
            f'padding-top:4px;white-space:nowrap;">{html.escape(value)}</div></td>')


def _signature_line_html():
    """Signature line with the phone number kept on one line on narrow screens."""
    line = html.escape(SIGNATURE_LINE)
    phone = "027 533 3981"
    return line.replace(phone, f'<span style="white-space:nowrap;">{phone}</span>')


def render_html(name, start, duration, meet_url, header_src=f"cid:{HEADER_CID}", icon_src=f"cid:{ICON_CID}"):
    """The designed email. header_src/icon_src let the in-app preview point at the files on disk."""
    first = html.escape(_first_name(name))
    tiles = (_tile("Date", _date_text(start)) + _tile("Time", fmt_clock_time(start))
             + _tile("Length", fmt_duration(duration)))
    if meet_url:
        url = html.escape(meet_url, quote=True)
        shown = html.escape(meet_url.replace("https://", "").replace("http://", ""))
        meet_block = f'''
<tr><td class="ct-px" style="padding:0 48px 0 48px;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
    <td bgcolor="{PLUM}" style="border-radius:10px;">
      <a href="{url}" target="_blank" style="display:inline-block;padding:14px 26px;font-family:{SANS};font-size:15px;font-weight:600;color:#FFFFFF;text-decoration:none;border-radius:10px;">Join on Google Meet</a>
    </td></tr></table>
</td></tr>
<tr><td class="ct-px" style="padding:24px 48px 0 48px;font-family:{SANS};font-size:13px;line-height:20px;color:{MUTED};">
  Or paste this link into your browser:<br>
  <a href="{url}" target="_blank" style="color:{PLUM};text-decoration:underline;">{shown}</a>
</td></tr>'''
    else:
        meet_block = f'''
<tr><td class="ct-px" style="padding:0 48px 0 48px;font-family:{SANS};font-size:15px;line-height:24px;color:{MUTED};">
  [Meet link pending]
</td></tr>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Your lesson is booked</title>
<style>
  @media only screen and (max-width: 480px) {{
    .ct-outer {{ padding: 16px 8px !important; }}
    .ct-px {{ padding-left: 24px !important; padding-right: 24px !important; }}
    .ct-tile {{ display: block !important; width: 100% !important; padding: 0 0 14px 0 !important; }}
  }}
</style>
<link href="https://fonts.googleapis.com/css2?family=Roboto+Slab:wght@700;800&amp;family=Inter:wght@400;600&amp;display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{CANVAS}" style="background:{CANVAS};">
<tr><td class="ct-outer" align="center" style="padding:40px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#FFFFFF"
       style="width:100%;max-width:600px;background:#FFFFFF;border-radius:14px;border-collapse:separate;overflow:hidden;">

<tr><td bgcolor="{PLUM}" style="background:{PLUM};border-radius:14px 14px 0 0;line-height:0;font-size:0;">
  <img src="{header_src}" width="600" alt="Croft Tutoring"
       style="display:block;width:100%;max-width:600px;height:auto;border:0;border-radius:14px 14px 0 0;">
</td></tr>

<tr><td class="ct-px" style="padding:44px 48px 0 48px;font-family:{SANS};font-size:16px;line-height:26px;color:{INK};">Hi {first},</td></tr>
<tr><td class="ct-px" style="padding:24px 48px 0 48px;font-family:{SLAB};font-size:26px;font-weight:700;line-height:32px;color:{DEEP};">Your lesson is booked</td></tr>

<tr><td class="ct-px" style="padding:24px 48px 24px 48px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PAPER}"
         style="background:{PAPER};border-radius:12px;border-collapse:separate;">
    <tr><td style="padding:24px 18px 24px 26px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{tiles}</tr></table>
    </td></tr>
  </table>
</td></tr>
{meet_block}
<tr><td class="ct-px" style="padding:24px 48px 0 48px;font-family:{SANS};font-size:16px;line-height:26px;color:{INK};">{html.escape(PREP_LINE)}</td></tr>

<tr><td class="ct-px" style="padding:24px 48px 40px 48px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr><td style="border-top:1px solid {BLUSH_RULE};padding-top:24px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td valign="middle" style="padding-right:16px;line-height:0;">
          <img src="{icon_src}" width="48" height="48" alt="" style="display:block;width:48px;height:48px;border:0;">
        </td>
        <td valign="middle">
          <div style="font-family:{SLAB};font-size:16px;font-weight:700;line-height:22px;color:{DEEP};">{html.escape(SIGNATURE_NAME)}</div>
          <div style="font-family:{SANS};font-size:13px;line-height:20px;color:{MUTED};">{_signature_line_html()}</div>
        </td>
      </tr></table>
    </td></tr>
  </table>
</td></tr>

<tr><td class="ct-px" bgcolor="{PAPER}" style="background:{PAPER};padding:18px 48px;font-family:{SANS};font-size:12px;line-height:18px;color:{MUTED};border-radius:0 0 14px 14px;">{html.escape(FOOTER_LINE)}</td></tr>

</table>
</td></tr>
</table>
</body>
</html>'''


def render_preview_html(name, start, duration, meet_url):
    """Same email with the images pointing at the files on disk, for the in-app preview."""
    return render_html(name, start, duration, meet_url,
                       header_src=HEADER_PATH.as_uri(), icon_src=ICON_PATH.as_uri())
