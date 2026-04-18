"""Calendar tool implementations — multi-account Google Calendar wrappers.

Token-efficient by design: list_events returns compact event shape without
attendee photos, conference phone numbers, or recurrence rules unless asked.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from accounts import service

DEFAULT_TZ = "America/Bogota"


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _parse_time(value: str) -> str:
    """Normalize a user-supplied time string to RFC3339 for Google Calendar."""
    value = value.strip()
    if not value:
        raise ValueError("Empty time value")

    shortcuts = {
        "now": datetime.now(timezone.utc),
        "today": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0),
        "tomorrow": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=1),
    }
    if value.lower() in shortcuts:
        return shortcuts[value.lower()].isoformat()

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError as e:
        raise ValueError(f"Can't parse time '{value}'. Use ISO 8601 or 'now'/'today'/'tomorrow'.") from e


def _summarize_event(event: dict, verbose: bool = False) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    attendees = event.get("attendees", []) or []

    out: dict[str, Any] = {
        "id": event["id"],
        "summary": event.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "status": event.get("status"),
        "organizer": (event.get("organizer") or {}).get("email"),
        "location": event.get("location"),
        "attendee_count": len(attendees),
        "hangout_link": event.get("hangoutLink"),
        "html_link": event.get("htmlLink"),
    }
    if verbose:
        out["description"] = event.get("description", "")
        out["attendees"] = [
            {
                "email": a.get("email"),
                "response": a.get("responseStatus"),
                "optional": a.get("optional", False),
                "organizer": a.get("organizer", False),
            }
            for a in attendees
        ]
        out["recurrence"] = event.get("recurrence")
        out["conference"] = event.get("conferenceData")
    return out


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def list_calendars(account: str | None = None) -> list[dict]:
    svc = service("calendar", "v3", account=account)
    resp = svc.calendarList().list().execute()
    return [
        {
            "id": c["id"],
            "summary": c.get("summary"),
            "primary": c.get("primary", False),
            "access_role": c.get("accessRole"),
            "time_zone": c.get("timeZone"),
        }
        for c in resp.get("items", [])
    ]


def list_events(
    account: str | None = None,
    calendar_id: str = "primary",
    time_min: str = "now",
    time_max: str | None = None,
    days_ahead: int = 7,
    query: str | None = None,
    max_results: int = 25,
    verbose: bool = False,
) -> list[dict]:
    svc = service("calendar", "v3", account=account)
    t_min = _parse_time(time_min)
    if time_max:
        t_max = _parse_time(time_max)
    else:
        t_max = (datetime.fromisoformat(t_min.replace("Z", "+00:00")) + timedelta(days=days_ahead)).isoformat()

    params: dict[str, Any] = {
        "calendarId": calendar_id,
        "timeMin": t_min,
        "timeMax": t_max,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": min(max(max_results, 1), 100),
    }
    if query:
        params["q"] = query

    resp = svc.events().list(**params).execute()
    return [_summarize_event(e, verbose=verbose) for e in resp.get("items", [])]


def create_event(
    summary: str,
    start: str,
    end: str,
    account: str | None = None,
    calendar_id: str = "primary",
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    time_zone: str = DEFAULT_TZ,
    send_updates: str = "all",
    add_meet: bool = False,
) -> dict:
    svc = service("calendar", "v3", account=account)

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": _parse_time(start), "timeZone": time_zone},
        "end": {"dateTime": _parse_time(end), "timeZone": time_zone},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    if add_meet:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-{int(datetime.now().timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    params = {"calendarId": calendar_id, "body": body, "sendUpdates": send_updates}
    if add_meet:
        params["conferenceDataVersion"] = 1

    event = svc.events().insert(**params).execute()
    return _summarize_event(event, verbose=True)


def update_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees_add: list[str] | None = None,
    attendees_remove: list[str] | None = None,
    time_zone: str = DEFAULT_TZ,
    send_updates: str = "all",
) -> dict:
    svc = service("calendar", "v3", account=account)
    event = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()

    if summary is not None:
        event["summary"] = summary
    if start is not None:
        event["start"] = {"dateTime": _parse_time(start), "timeZone": time_zone}
    if end is not None:
        event["end"] = {"dateTime": _parse_time(end), "timeZone": time_zone}
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location

    current = {a["email"].lower(): a for a in event.get("attendees", []) or []}
    if attendees_add:
        for email in attendees_add:
            current.setdefault(email.lower(), {"email": email})
    if attendees_remove:
        for email in attendees_remove:
            current.pop(email.lower(), None)
    if attendees_add or attendees_remove:
        event["attendees"] = list(current.values())

    updated = (
        svc.events()
        .update(calendarId=calendar_id, eventId=event_id, body=event, sendUpdates=send_updates)
        .execute()
    )
    return _summarize_event(updated, verbose=True)


def delete_event(
    event_id: str,
    account: str | None = None,
    calendar_id: str = "primary",
    send_updates: str = "all",
) -> dict:
    svc = service("calendar", "v3", account=account)
    svc.events().delete(
        calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates
    ).execute()
    return {"event_id": event_id, "status": "deleted"}


def freebusy(
    time_min: str,
    time_max: str,
    account: str | None = None,
    emails: list[str] | None = None,
) -> dict:
    """Check busy windows for a set of calendars. emails defaults to [account]."""
    svc = service("calendar", "v3", account=account)
    items = [{"id": e} for e in (emails or ["primary"])]
    resp = (
        svc.freebusy()
        .query(
            body={
                "timeMin": _parse_time(time_min),
                "timeMax": _parse_time(time_max),
                "items": items,
            }
        )
        .execute()
    )
    return {
        email: data.get("busy", [])
        for email, data in resp.get("calendars", {}).items()
    }


def respond(
    event_id: str,
    response: str,
    account: str | None = None,
    calendar_id: str = "primary",
) -> dict:
    """Respond accepted / declined / tentative to an event you're invited to."""
    if response not in {"accepted", "declined", "tentative"}:
        raise ValueError("response must be one of: accepted, declined, tentative")

    svc = service("calendar", "v3", account=account)
    event = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()

    me = (account or "").lower()
    if not me:
        from accounts import default_account
        me = default_account().lower()

    attendees = event.get("attendees", []) or []
    found = False
    for a in attendees:
        if a.get("email", "").lower() == me:
            a["responseStatus"] = response
            found = True
            break
    if not found:
        attendees.append({"email": me, "responseStatus": response})
    event["attendees"] = attendees

    updated = svc.events().update(
        calendarId=calendar_id, eventId=event_id, body=event, sendUpdates="all"
    ).execute()
    return {"event_id": event_id, "response": response, "status": _summarize_event(updated)["status"]}
