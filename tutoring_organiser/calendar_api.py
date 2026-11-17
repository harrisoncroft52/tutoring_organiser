"""Google Calendar: creating events and Meet links, syncing lessons both ways, deleting events."""
import time
import uuid
from datetime import datetime, timedelta, timezone

from .config import DT_FMT, TIMEZONE
from .google_auth import _service, friendly_error, GoogleError, HttpError
from .helpers import auckland_now


def meet_from_event(ev):
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    return ""


def _meet_request_status(ev):
    return (((ev.get("conferenceData") or {}).get("createRequest") or {}).get("status") or {}).get("statusCode", "")


def _meet_request_body():
    return {"conferenceData": {"createRequest": {
        "requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}}


def wait_for_meet(cal, event_id, ev, attempts=15, delay=1.0, sleep=time.sleep):
    """Google creates Meet links asynchronously: the insert reply can say 'pending'. Poll until the link
    exists. If the request failed (or was never made) ask once more. Returns (event, url); url is '' if
    Google is still working after all attempts - the event is fine, just check again later."""
    asked_again = False
    for _ in range(attempts):
        url = meet_from_event(ev)
        if url:
            return ev, url
        status = _meet_request_status(ev)
        if status != "pending":
            if asked_again:
                raise GoogleError("Google couldn't generate a Meet link for this event "
                                  f"(status: {status or 'none'}). Your Calendar event exists; try 'Check for Meet "
                                  "Link' again in a minute.")
            ev = cal.events().patch(calendarId="primary", eventId=event_id, conferenceDataVersion=1,
                                    sendUpdates="none", body=_meet_request_body()).execute()
            asked_again = True
            continue
        sleep(delay)
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()
    return ev, meet_from_event(ev)


def calendar_create_event(creds, *, event_id, summary, description, start, duration, emails,
                          service=None, sleep=time.sleep):
    """Create (or, if event_id already exists, resume) one event with a Meet link.
    sendUpdates='none' stops Google emailing the attendee: the only email is the one you review and send.
    The caller-chosen event_id makes the insert idempotent - a retry after a lost reply can't duplicate it."""
    cal = service or _service("calendar", "v3", creds)
    end = start + timedelta(minutes=duration)
    body = {
        "id": event_id, "summary": summary, "description": description,
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "attendees": [{"email": e} for e in emails],
        **_meet_request_body(),
    }
    try:
        ev = cal.events().insert(calendarId="primary", body=body, conferenceDataVersion=1,
                                 sendUpdates="none").execute()
    except HttpError as e:
        if getattr(e.resp, "status", None) != 409:
            raise
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()   # created by an earlier attempt
        if ev.get("status") == "cancelled":
            raise GoogleError("That Calendar event was deleted in Google Calendar. Start a new invitation.")
    result = {"event_id": ev["id"], "html_link": ev.get("htmlLink", ""), "meet_url": "", "warning": ""}
    try:
        ev, result["meet_url"] = wait_for_meet(cal, ev["id"], ev, sleep=sleep)
    except Exception as e:      # the event exists; don't lose it just because the Meet lookup failed
        result["warning"] = friendly_error(e)
    return result


def calendar_resume_event(creds, event_id, service=None, sleep=time.sleep):
    """Look up an existing event and (re)fetch its Meet link. Never creates a new event."""
    cal = service or _service("calendar", "v3", creds)
    try:
        ev = cal.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        if getattr(e.resp, "status", None) == 404:
            raise GoogleError("That event no longer exists in Google Calendar.")
        raise
    if ev.get("status") == "cancelled":
        raise GoogleError("That event was cancelled in Google Calendar.")
    ev, url = wait_for_meet(cal, event_id, ev, sleep=sleep)
    return {"event_id": event_id, "meet_url": url, "html_link": ev.get("htmlLink", ""), "warning": ""}


def calendar_find_student_events(creds, student_name, service=None):
    """Find upcoming primary-calendar bookings titled 'Tutoring <student name>'."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(TIMEZONE))
    except Exception:
        now = datetime.now().astimezone()
    cal = service or _service("calendar", "v3", creds)
    token, found = None, []
    while True:
        response = cal.events().list(calendarId="primary", q=f"Tutoring {student_name}",
                                     timeMin=now.isoformat(),
                                     singleEvents=True, orderBy="startTime", maxResults=250, pageToken=token).execute()
        for ev in response.get("items", []):
            summary = (ev.get("summary") or "").strip()
            prefix = f"Tutoring {student_name}".casefold()
            remainder = summary.casefold()
            matches = remainder == prefix or any(remainder.startswith(prefix + d) for d in (" ", "-", "–", "—", ":", "|", "("))
            if matches and ev.get("status") != "cancelled":
                found.append(_remote_event_record(ev))
        token = response.get("nextPageToken")
        if not token:
            return found


def calendar_event_datetimes(ev):
    """Convert a Google event's timed or all-day bounds to naive Auckland datetimes."""
    try:
        from zoneinfo import ZoneInfo
        calendar_tz = ZoneInfo(TIMEZONE)
    except Exception:
        calendar_tz = datetime.now().astimezone().tzinfo
    def convert(value):
        if "T" not in value:
            return datetime.fromisoformat(value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(calendar_tz).replace(tzinfo=None)
        return parsed
    return (convert((ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")),
            convert((ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")))


def _utc_stamp(value):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return ""


def _calendar_tutoring_event(row, student_name, event_id=None):
    start = datetime.strptime(row["start"], DT_FMT)
    body = {
        "summary": f"Tutoring {student_name}",
        "description": f"Online tutoring lesson with {student_name}.",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:00"), "timeZone": TIMEZONE},
        "end": {"dateTime": (start + timedelta(minutes=int(row["duration"]))).strftime("%Y-%m-%dT%H:%M:00"),
                "timeZone": TIMEZONE},
    }
    if row["status"] == "cancelled":
        body["status"] = "cancelled"
    if event_id:
        body["id"] = event_id
    return body


def _remote_event_record(ev):
    start, end = calendar_event_datetimes(ev)
    return {"event_id": ev["id"], "start": start,
            "duration": max(5, int(round((end - start).total_seconds() / 60))),
            "meet_url": meet_from_event(ev), "updated": _utc_stamp(ev.get("updated", "")),
            "status": "cancelled" if ev.get("status") == "cancelled" else "scheduled",
            "summary": ev.get("summary", "")}


def calendar_sync_tutoring(creds, lessons, students):
    """Two-way sync for primary-calendar items titled 'Tutoring <student>'.
    Local changes win if newer than the last Google update; otherwise Google changes win.
    Only upcoming, scheduled unlinked local lessons are created in Google Calendar.
    """
    cal = _service("calendar", "v3", creds)
    all_events, token = [], None
    while True:
        response = cal.events().list(calendarId="primary", q="Tutoring", singleEvents=True,
                                     showDeleted=True, maxResults=2500, pageToken=token).execute()
        all_events.extend(response.get("items", []))
        token = response.get("nextPageToken")
        if not token:
            break
    by_id = {e["id"]: e for e in all_events if e.get("id")}
    tutoring = [e for e in all_events if (e.get("summary") or "").casefold().startswith("tutoring ")]
    actions, consumed_local, consumed_remote, missing_ids = [], set(), set(), []

    def student_for_event(event):
        summary = (event.get("summary") or "").strip()
        if not summary.casefold().startswith("tutoring "):
            return None
        remainder = summary[len("Tutoring "):].strip().casefold()
        matches = [s for s in students if remainder == s["name"].strip().casefold()
                   or any(remainder.startswith(s["name"].strip().casefold() + delimiter)
                          for delimiter in (" ", "-", "–", "—", ":", "|", "("))]
        return max(matches, key=lambda s: len(s["name"])) if matches else None

    def push_local(row, student, event=None, deterministic_id=None):
        event_id = row.get("calendar_event_id") or (event or {}).get("id") or deterministic_id
        body = _calendar_tutoring_event(row, student["name"], event_id if not event else None)
        if event:
            if row["status"] == "cancelled":
                result = cal.events().patch(calendarId="primary", eventId=event["id"], body={"status": "cancelled"},
                                            sendUpdates="none").execute()
            else:
                result = cal.events().patch(calendarId="primary", eventId=event["id"], body=body,
                                            sendUpdates="none").execute()
        else:
            try:
                result = cal.events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
            except HttpError as exc:
                if getattr(exc.resp, "status", None) != 409:
                    raise
                result = cal.events().get(calendarId="primary", eventId=event_id).execute()
        actions.append({"sid": student["id"], "event": _remote_event_record(result)})
        if event:
            consumed_remote.add(event["id"])
        consumed_local.add(row["id"])

    # Existing links are stable even if the event has moved.
    for row in lessons:
        event_id = row.get("calendar_event_id")
        if not event_id:
            continue
        event = by_id.get(event_id)
        student = next((s for s in students if s["id"] == row["student_id"]), None)
        if not student:
            continue
        if not event:
            missing_ids.append(event_id)
            consumed_local.add(row["id"])
            continue
        consumed_remote.add(event_id)
        if event.get("status") == "cancelled":
            actions.append({"sid": student["id"], "event": {
                "event_id": event_id, "start": datetime.strptime(row["start"], DT_FMT),
                "duration": row["duration"], "meet_url": row["meet_url"] or "",
                "updated": _utc_stamp(event.get("updated", "")), "status": "cancelled"}})
            consumed_local.add(row["id"])
            continue
        remote_updated = _utc_stamp(event.get("updated", ""))
        local_updated = _utc_stamp(row.get("local_updated_at", ""))
        event_student = student_for_event(event)
        if (not event_student or event_student["id"] != student["id"]) and local_updated and remote_updated and local_updated > remote_updated:
            push_local(row, student, event)
        elif not event_student:
            missing_ids.append(event_id)
            consumed_local.add(row["id"])
        elif row["status"] == "cancelled" and event.get("status") != "cancelled":
            push_local(row, student, event)
        elif local_updated and remote_updated and local_updated > remote_updated:
            push_local(row, student, event)
        else:
            actions.append({"sid": event_student["id"], "event": _remote_event_record(event)})
            consumed_local.add(row["id"])

    # Unlinked local bookings either pair by student+start with Google or get created if upcoming.
    unlinked = [r for r in lessons if not r.get("calendar_event_id") and r["id"] not in consumed_local]
    for event in tutoring:
        if event["id"] in consumed_remote:
            continue
        student = student_for_event(event)
        if not student or event.get("status") == "cancelled":
            continue
        remote_record = _remote_event_record(event)
        start_s = remote_record["start"].strftime(DT_FMT)
        local = next((r for r in unlinked if r["id"] not in consumed_local
                      and r["student_id"] == student["id"] and r["start"] == start_s), None)
        if local:
            local_updated = _utc_stamp(local.get("local_updated_at", ""))
            remote_updated = remote_record["updated"]
            if local_updated and remote_updated and local_updated > remote_updated:
                push_local(local, student, event)
            else:
                actions.append({"sid": student["id"], "event": remote_record})
                consumed_local.add(local["id"]); consumed_remote.add(event["id"])
        else:
            actions.append({"sid": student["id"], "event": remote_record})
            consumed_remote.add(event["id"])

    now = auckland_now()
    for row in unlinked:
        if row["id"] in consumed_local or row["status"] == "cancelled":
            continue
        start = datetime.strptime(row["start"], DT_FMT)
        if start < now:
            continue
        student = next((s for s in students if s["id"] == row["student_id"]), None)
        if student:
            deterministic_id = uuid.uuid5(uuid.NAMESPACE_URL, f"tutor-tracker-lesson-{row['id']}").hex
            push_local(row, student, deterministic_id=deterministic_id)
    return {"actions": actions, "missing_ids": missing_ids,
            "seen": len(tutoring), "matched": len(actions), "unmatched": 0}


def calendar_delete_event(creds, event_id, service=None):
    # A local-only lesson has nothing to delete remotely. Keep this guard at the
    # API boundary as well as in callers so an empty id can never reach Google.
    if not event_id:
        return False
    cal = service or _service("calendar", "v3", creds)
    try:
        cal.events().delete(calendarId="primary", eventId=event_id, sendUpdates="none").execute()
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status not in (404, 410):
            # A stale/malformed local event id can produce 400 instead of Google's usual
            # 404 for a deleted event. Confirm with get() before treating it as already gone.
            try:
                cal.events().get(calendarId="primary", eventId=event_id).execute()
            except HttpError as check_exc:
                if getattr(check_exc.resp, "status", None) not in (404, 410):
                    raise exc
            else:
                raise exc
    return True
