"""Invitation emails: templates, filling them in, building the message and sending through Gmail."""
import base64
import html
import json
import re
from email.message import EmailMessage
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QTextDocument

from .google_auth import _service, GoogleError
from .helpers import auckland_now, fmt_clock_time, fmt_duration


EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MEET_PENDING = "[Meet link pending]"
DEFAULT_SUBJECT = "Tutoring lesson \u2013 {date} at {time}"


DEFAULT_MESSAGE = (
    "Hi {first_name},\n\n"
    "Your tutoring lesson is booked for {date} at {time} (New Zealand time), "
    "and will run for {duration}.\n\n"
    "Join on Google Meet:\n{meet_link}\n\n"
    "See you then!"
)


def parse_emails(text):
    """'a@x.com, b@y.com' -> ['a@x.com', 'b@y.com'] (de-duplicated, order kept)."""
    out, seen = [], set()
    for p in re.split(r"[,;\s]+", text or ""):
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def invalid_emails(parts):
    return [p for p in parts if not EMAIL_RE.match(p)]


def lesson_date_phrase(start, today=None):
    """'today' when the lesson is today (Auckland), otherwise '6 November' (no year, no weekday)."""
    today = today or auckland_now().date()
    if start.date() == today:
        return "today"
    return f"{start.day} {start:%B}"


def render_invite(subject_tpl, message_tpl, name, start, duration, meet_url):
    """Fill {placeholders}. Unknown {words} are left alone (no KeyErrors from stray braces)."""
    ctx = {
        "name": name,
        "first_name": (name.split() or [name])[0] if name else "",
        "date": lesson_date_phrase(start),
        "time": fmt_clock_time(start),
        "duration": fmt_duration(duration),
        "meet_link": meet_url or MEET_PENDING,
    }
    def fill(t):
        for k, v in ctx.items():
            t = t.replace("{" + k + "}", v)
        return t
    return fill(subject_tpl), fill(message_tpl)


def render_invite_html(html_tpl, name, start, duration, meet_url):
    values = {
        "name": name,
        "first_name": (name.split() or [name])[0] if name else "",
        "date": lesson_date_phrase(start),
        "time": fmt_clock_time(start),
        "duration": fmt_duration(duration),
    }
    for key, value in values.items():
        html_tpl = html_tpl.replace("{" + key + "}", html.escape(str(value)))
    # The Meet link becomes a real clickable hyperlink (the plain-text part keeps the bare URL).
    if meet_url:
        safe = html.escape(meet_url, quote=True)
        link = f'<a href="{safe}">{safe}</a>'
    else:
        link = html.escape(MEET_PENDING)
    return html_tpl.replace("{meet_link}", link)


def inline_template_images(store):
    try:
        value = json.loads(store.setting("invite_inline_images", "{}"))
        return {str(cid): str(path) for cid, path in value.items()
                if isinstance(cid, str) and isinstance(path, str)} if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def register_inline_image_resources(editor, images):
    for cid, path in images.items():
        if Path(path).is_file():
            editor.document().addResource(QTextDocument.ImageResource, QUrl("cid:" + cid), QImage(path))


IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}


IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.gif *.webp)"
MAX_ATTACH_BYTES = 15 * 1024 * 1024      # Gmail's limit is 25 MB for the whole message; base64 adds ~33%


def embed_width(path, max_width=600):
    """Display width (px) for an image embedded in an email. Images wider than 600 px are treated as 2x
    (retina) graphics and shown at half size, so the 1200 px Croft Tutoring header shows at 600 px and
    stays sharp. Smaller images show at their own size."""
    w = QImage(str(path)).width()
    if w <= 0:
        return 360
    if w > max_width:
        w = w // 2
    return min(w, max_width)


def check_attachments(paths):
    """Raise GoogleError (with a readable message) if any attachment is missing, not an image, or too big."""
    total = 0
    for path in paths:
        pp = Path(path)
        if not pp.is_file():
            raise GoogleError(f"Can't find the attachment:\n{path}")
        if pp.suffix.lower() not in IMAGE_EXTS:
            raise GoogleError(f"Only PNG, JPG, GIF and WebP images can be attached:\n{pp.name}")
        total += pp.stat().st_size
    if total > MAX_ATTACH_BYTES:
        raise GoogleError(f"The attachments total {total / 1048576:.1f} MB. Keep them under "
                          f"{MAX_ATTACH_BYTES // 1048576} MB or Gmail may reject the email.")
    return total


def build_raw_email(to_list, subject, body, attachments=(), html_body="", inline_images=()):
    msg = EmailMessage()
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
        html_part = msg.get_payload()[-1]
        for path, content_id in inline_images:
            pp = Path(path)
            maintype, subtype = IMAGE_EXTS[pp.suffix.lower()].split("/")
            html_part.add_related(pp.read_bytes(), maintype=maintype, subtype=subtype, cid=f"<{content_id}>")
    for path in attachments:
        pp = Path(path)
        maintype, subtype = IMAGE_EXTS.get(pp.suffix.lower(), "image/png").split("/")
        msg.add_attachment(pp.read_bytes(), maintype=maintype, subtype=subtype, filename=pp.name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def gmail_send(creds, to_list, subject, body, attachments=(), html_body="", inline_images=(), service=None):
    """Send an invitation with optional attachments and MIME-embedded images.
    Returns Gmail's message id."""
    check_attachments(list(attachments) + [p for p, _ in inline_images])
    svc = service or _service("gmail", "v1", creds)
    resp = svc.users().messages().send(
        userId="me", body={"raw": build_raw_email(to_list, subject, body, attachments,
                                                   html_body, inline_images)}).execute()
    if not resp.get("id"):
        raise GoogleError("Gmail didn't confirm that the email was sent. Check your Sent folder before retrying.")
    return resp["id"]
