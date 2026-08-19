"""Calendar tool implementations — multi-account Google Calendar wrappers.

Token-efficient by design: list_events returns compact event shape without
attendee photos, conference phone numbers, or recurrence rules unless asked.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from accounts import service

# Last resort, reached only when the machine will not say where it is and
# nothing was configured. UTC is the honest answer to "I don't know" -- it is
# obviously wrong to whoever reads it back, where a plausible-looking city
# would just be quietly wrong. Set GWS_TIME_ZONE to settle it.
FALLBACK_TZ = "UTC"


def _is_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def _tz_from_etc_localtime() -> str | None:
    """The zone macOS and Linux both encode as a symlink into the tz tree."""
    try:
        parts = Path("/etc/localtime").resolve().parts
    except OSError:
        return None
    if "zoneinfo" not in parts:
        return None
    name = "/".join(parts[parts.index("zoneinfo") + 1 :])
    return name if name and _is_zone(name) else None


def _tz_from_tzlocal() -> str | None:
    """The zone tzlocal reads out of the platform's own settings.

    This is the only tier that answers on Windows, which has no /etc/localtime
    and names its zones in its own vocabulary ("SA Western Standard Time")
    rather than IANA's. tzlocal reads the registry and maps it through the CLDR
    table; asking it directly for a ZoneInfo name is far better than shipping a
    copy of that mapping here and letting it rot.

    Imported lazily and defensively: it is a dependency, but an install that
    predates it must degrade to the fallback rather than fail to start.
    """
    try:
        import tzlocal
    except ImportError:
        return None
    try:
        name = tzlocal.get_localzone_name()
    except Exception:  # noqa: BLE001 - tzlocal raises several unrelated types
        return None
    return name if name and _is_zone(name) else None


def _local_tz_name() -> str:
    """The machine's IANA zone, e.g. 'America/Caracas'.

    Every naive time a person types is resolved here, so guessing wrong moves
    real meetings. Order: an explicit GWS_TIME_ZONE, then TZ, then
    /etc/localtime, then tzlocal, then the fallback.

    The tzlocal tier is what makes this correct on Windows. Before it, every
    Windows install fell all the way through to UTC and quietly booked "3pm"
    at 3pm UTC -- the same class of bug as the naive-datetime one these zones
    exist to fix, and just as silent: nothing raises, the event is created, and
    only the hour is wrong. Note that the UTC fallback is a real zone, so a
    test that only asserts "the default is a valid zone" stays green through
    the whole failure. What is asserted instead is that a machine whose zone is
    knowable does not answer UTC.

    /etc/localtime is kept ahead of tzlocal so nothing changes for the macOS
    and Linux installs that already resolve correctly today.
    """
    for candidate in (os.environ.get("GWS_TIME_ZONE"), os.environ.get("TZ")):
        if candidate and _is_zone(candidate):
            return candidate
    for probe in (_tz_from_etc_localtime, _tz_from_tzlocal):
        found = probe()
        if found:
            return found
    return FALLBACK_TZ


DEFAULT_TZ = _local_tz_name()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as e:
        raise ValueError(
            f"Unknown time zone {name!r}. Use an IANA name like 'America/Caracas'. "
            "On Windows the zone database is a separate package: pip install tzdata."
        ) from e


def _parse_dt(value: str, tz_name: str | None = None) -> datetime:
    """Parse a user-supplied time into an aware datetime.

    A time with no offset ("2026-08-18T09:00:00") means nine in the morning
    where the person is, so it is resolved in `tz_name`. Stamping it as UTC
    instead silently moved events by the size of the local offset -- and
    because Google lets the offset inside dateTime override the separate
    timeZone field, setting timeZone correctly did not save it. It created
    without an error, which is why this went unnoticed.

    A time that already carries an offset is authoritative and passes through
    untouched.
    """
    tz = _zone(tz_name or DEFAULT_TZ)

    value = value.strip()
    if not value:
        raise ValueError("Empty time value")

    key = value.lower()
    if key in ("now", "today", "tomorrow"):
        now = datetime.now(tz)
        if key == "now":
            return now
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight if key == "today" else midnight + timedelta(days=1)

    normalized = value[:-1] + "+00:00" if value[-1:] in ("Z", "z") else value
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError(
            f"Can't parse time '{value}'. Use ISO 8601 or 'now'/'today'/'tomorrow'."
        ) from e
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _parse_time(value: str, tz_name: str | None = None) -> str:
    """Normalize a user-supplied time string to RFC3339 for Google Calendar."""
    return _parse_dt(value, tz_name).isoformat()


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
    time_zone: str | None = None,
) -> list[dict]:
    svc = service("calendar", "v3", account=account)
    # 'today' has to mean today where the person is. Anchored to UTC instead,
    # the window opens in yesterday evening for everyone west of Greenwich and
    # the day comes back missing its last few hours.
    dt_min = _parse_dt(time_min, time_zone)
    t_min = dt_min.isoformat()
    if time_max:
        t_max = _parse_time(time_max, time_zone)
    else:
        # astimezone re-normalizes the offset if the window crosses a DST change.
        t_max = (dt_min + timedelta(days=days_ahead)).astimezone(dt_min.tzinfo).isoformat()

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
    time_zone: str | None = None,
    send_updates: str = "all",
    add_meet: bool = False,
) -> dict:
    svc = service("calendar", "v3", account=account)
    # One zone for both halves: the offset written into dateTime has to agree
    # with timeZone, because Google resolves the event by the offset and keeps
    # timeZone only for expanding recurrences.
    tz = time_zone or DEFAULT_TZ

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": _parse_time(start, tz), "timeZone": tz},
        "end": {"dateTime": _parse_time(end, tz), "timeZone": tz},
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
    time_zone: str | None = None,
    send_updates: str = "all",
) -> dict:
    svc = service("calendar", "v3", account=account)
    event = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()
    tz = time_zone or DEFAULT_TZ

    if summary is not None:
        event["summary"] = summary
    if start is not None:
        event["start"] = {"dateTime": _parse_time(start, tz), "timeZone": tz}
    if end is not None:
        event["end"] = {"dateTime": _parse_time(end, tz), "timeZone": tz}
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
    time_zone: str | None = None,
) -> dict:
    """Check busy windows for a set of calendars. emails defaults to [account]."""
    svc = service("calendar", "v3", account=account)
    items = [{"id": e} for e in (emails or ["primary"])]
    resp = (
        svc.freebusy()
        .query(
            body={
                "timeMin": _parse_time(time_min, time_zone),
                "timeMax": _parse_time(time_max, time_zone),
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
