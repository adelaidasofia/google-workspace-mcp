"""Optional guests reach Google as `optional: true`, and no one else changes.

The Calendar API marks a guest optional with `{"email": ..., "optional": true}`
in the event's attendees list. create_event and update_event built every guest
as a bare `{"email": ...}`, so "invite them, but as optional" could not be said
through the tools at all and had to be done by hand against the raw API.

Nothing here touches the network. The service factory is monkeypatched with a
fake that records the request that would have gone to Google, the same seam
test_calendar_time.py uses. The tool-level tests go in through an in-memory MCP
client, because a parameter the tool schema rejects, or accepts and never
forwards, is as missing as one that was never written.
"""

import asyncio
import copy

import pytest

import calendar_tools as C

HOST = {"email": "host@example.com", "organizer": True, "responseStatus": "accepted"}
REQUIRED = "required@example.com"
OPTIONAL = "optional@example.com"
WHEN = {"start": "2026-10-01T09:00:00", "end": "2026-10-01T10:00:00", "time_zone": "UTC"}


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeCalendar:
    """Stands in for `service("calendar", "v3")`.

    `stored` is the event `get` hands back; `sent` is whatever the last insert
    or update would have sent to Google.
    """

    def __init__(self, stored=None):
        self.stored = stored or {}
        self.sent = {}

    def events(self):
        return self

    def insert(self, **params):
        self.sent = params
        return _Exec({"id": "evt-1", **params["body"]})

    def get(self, **params):
        # A copy, so the tool mutating what it fetched cannot rewrite the
        # fixture the test compares against.
        return _Exec(copy.deepcopy(self.stored))

    def update(self, **params):
        self.sent = params
        return _Exec(params["body"])


@pytest.fixture
def calendar(monkeypatch):
    def install(*attendees):
        fake = _FakeCalendar({"id": "evt-1", "summary": "Sync", "attendees": list(attendees)})
        monkeypatch.setattr(C, "service", lambda *a, **k: fake)
        return fake

    return install


def _sent_attendees(fake):
    return fake.sent["body"].get("attendees")


def _call_tool(name, arguments):
    from fastmcp import Client

    import server

    async def call():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(call())


# --------------------------------------------------------------------------
# create_event
# --------------------------------------------------------------------------


def test_create_event_sends_optional_guests_as_optional(calendar):
    fake = calendar()
    C.create_event(summary="Sync", attendees=[REQUIRED], optional_attendees=[OPTIONAL], **WHEN)

    assert _sent_attendees(fake) == [
        {"email": REQUIRED},
        {"email": OPTIONAL, "optional": True},
    ]


def test_create_event_accepts_optional_guests_on_their_own(calendar):
    fake = calendar()
    C.create_event(summary="Sync", optional_attendees=[OPTIONAL], **WHEN)

    assert _sent_attendees(fake) == [{"email": OPTIONAL, "optional": True}]


def test_create_event_guest_in_both_lists_is_invited_once_as_optional(calendar):
    """Listing everyone in attendees and then naming who is optional is a natural
    way to read the two parameters. It must not send the same guest twice."""
    fake = calendar()
    C.create_event(
        summary="Sync",
        attendees=[REQUIRED, "Optional@Example.com"],
        optional_attendees=[OPTIONAL],
        **WHEN,
    )

    assert _sent_attendees(fake) == [
        {"email": REQUIRED},
        {"email": OPTIONAL, "optional": True},
    ]


# --------------------------------------------------------------------------
# update_event
# --------------------------------------------------------------------------


def test_update_event_invites_a_new_guest_as_optional(calendar):
    fake = calendar(HOST)
    C.update_event(event_id="evt-1", attendees_add_optional=[OPTIONAL])

    assert _sent_attendees(fake) == [HOST, {"email": OPTIONAL, "optional": True}]


def test_update_event_marks_an_existing_guest_optional_and_keeps_their_rsvp(calendar):
    """Re-adding the guest would reset their response; flipping the flag must not."""
    guest = {"email": "guest@example.com", "responseStatus": "accepted"}
    fake = calendar(HOST, guest)
    C.update_event(event_id="evt-1", attendees_add_optional=["Guest@Example.com"])

    assert _sent_attendees(fake) == [HOST, {**guest, "optional": True}]


# --------------------------------------------------------------------------
# The MCP tools expose and forward the new parameters
# --------------------------------------------------------------------------


def test_cal_create_event_tool_forwards_optional_attendees(calendar):
    fake = calendar()
    _call_tool(
        "cal_create_event",
        {"summary": "Sync", "attendees": [REQUIRED], "optional_attendees": [OPTIONAL], **WHEN},
    )

    assert _sent_attendees(fake) == [
        {"email": REQUIRED},
        {"email": OPTIONAL, "optional": True},
    ]


def test_cal_update_event_tool_forwards_attendees_add_optional(calendar):
    fake = calendar(HOST)
    _call_tool("cal_update_event", {"event_id": "evt-1", "attendees_add_optional": [OPTIONAL]})

    assert _sent_attendees(fake) == [HOST, {"email": OPTIONAL, "optional": True}]


# --------------------------------------------------------------------------
# Must not regress: callers who never pass the new parameters
# --------------------------------------------------------------------------


def test_create_event_without_optional_guests_sends_bare_emails(calendar):
    fake = calendar()
    C.create_event(summary="Sync", attendees=[REQUIRED, OPTIONAL], **WHEN)

    assert _sent_attendees(fake) == [{"email": REQUIRED}, {"email": OPTIONAL}]


def test_create_event_with_no_guests_sends_no_attendees_field(calendar):
    fake = calendar()
    C.create_event(summary="Sync", **WHEN)

    assert "attendees" not in fake.sent["body"]


def test_update_event_attendees_add_still_invites_as_required(calendar):
    fake = calendar(HOST)
    C.update_event(event_id="evt-1", attendees_add=[REQUIRED])

    assert _sent_attendees(fake) == [HOST, {"email": REQUIRED}]


def test_update_event_attendees_add_leaves_an_existing_optional_guest_as_is(calendar):
    optional_guest = {"email": OPTIONAL, "optional": True, "responseStatus": "needsAction"}
    fake = calendar(HOST, optional_guest)
    C.update_event(event_id="evt-1", attendees_add=[OPTIONAL])

    assert _sent_attendees(fake) == [HOST, optional_guest]


def test_update_event_without_guest_changes_sends_the_guest_list_it_fetched(calendar):
    optional_guest = {"email": OPTIONAL, "optional": True, "responseStatus": "needsAction"}
    fake = calendar(HOST, optional_guest)
    C.update_event(event_id="evt-1", summary="Renamed")

    assert _sent_attendees(fake) == [HOST, optional_guest]
