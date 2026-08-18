"""The calendar's time layer decides what hour a meeting actually happens at.

A time typed without a UTC offset ("2026-08-18T09:00:00") used to be stamped
UTC. Google resolves an event by the offset inside `dateTime` and keeps the
separate `timeZone` field only for expanding recurrences, so setting timeZone
correctly did not rescue it: in Caracas (UTC-4) a 09:00 meeting was created at
05:00. Nothing raised, which is why it went unnoticed.

These pin the fix, and just as importantly the pass-through cases that must
keep working while it is in place.
"""

import pytest

import calendar_tools as C

# Nothing here touches the network: importing calendar_tools only builds the
# module, and the one test that exercises a real code path monkeypatches the
# service factory. Stubbing `accounts` in sys.modules would be the obvious
# shortcut and is a trap -- pytest shares one process, so the stub would still
# be installed when the credential and scope tests import the real thing.

CARACAS = "America/Caracas"


# --------------------------------------------------------------------------
# The reported bug
# --------------------------------------------------------------------------


def test_naive_time_is_read_in_the_target_zone_not_utc():
    assert C._parse_time("2026-08-18T09:00:00", CARACAS) == "2026-08-18T09:00:00-04:00"


def test_naive_and_explicit_offset_agree():
    """The two lines from the bug report must now produce the same instant."""
    naive = C._parse_time("2026-08-18T09:00:00", CARACAS)
    explicit = C._parse_time("2026-08-18T09:00:00-04:00", CARACAS)
    assert naive == explicit


def test_naive_time_is_never_silently_utc():
    """The specific regression: a zone that is not UTC must not yield +00:00."""
    for zone in (CARACAS, "America/Bogota", "Asia/Tokyo", "Europe/Madrid"):
        out = C._parse_time("2026-08-18T09:00:00", zone)
        assert not out.endswith("+00:00"), f"{zone} fell back to UTC: {out}"
        assert out.startswith("2026-08-18T09:00:00"), out


def test_event_body_offset_agrees_with_its_timezone_field(monkeypatch):
    """What actually reaches Google. The offset and timeZone must not disagree."""
    sent = {}

    class _Exec:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class _Events:
        def insert(self, **params):
            sent.update(params)
            body = params["body"]
            return _Exec({"id": "evt-1", "start": body["start"], "end": body["end"]})

    class _Svc:
        def events(self):
            return _Events()

    monkeypatch.setattr(C, "service", lambda *a, **k: _Svc())
    C.create_event(
        summary="Standup",
        start="2026-08-18T09:00:00",
        end="2026-08-18T10:00:00",
        time_zone=CARACAS,
    )

    assert sent["body"]["start"] == {
        "dateTime": "2026-08-18T09:00:00-04:00",
        "timeZone": CARACAS,
    }
    assert sent["body"]["end"]["dateTime"] == "2026-08-18T10:00:00-04:00"


# --------------------------------------------------------------------------
# Must not regress
# --------------------------------------------------------------------------


def test_explicit_offset_wins_over_the_zone_argument():
    """A caller who pinned an instant meant it; the zone must not re-interpret it."""
    assert (
        C._parse_time("2026-08-18T09:00:00-04:00", "Asia/Tokyo")
        == "2026-08-18T09:00:00-04:00"
    )


def test_trailing_z_still_means_utc():
    assert C._parse_time("2026-08-18T09:00:00Z", CARACAS) == "2026-08-18T09:00:00+00:00"


def test_date_only_is_local_midnight():
    assert C._parse_time("2026-08-18", CARACAS) == "2026-08-18T00:00:00-04:00"


@pytest.mark.parametrize("bad", ["", "   ", "next tuesday", "18/08/2026"])
def test_unparseable_values_still_raise(bad):
    with pytest.raises(ValueError):
        C._parse_time(bad, CARACAS)


# --------------------------------------------------------------------------
# Shortcuts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("shortcut", ["today", "tomorrow"])
def test_shortcuts_are_local_midnight_not_utc_midnight(shortcut):
    out = C._parse_dt(shortcut, CARACAS)
    assert (out.hour, out.minute, out.second) == (0, 0, 0)
    assert out.utcoffset().total_seconds() == -4 * 3600


def test_tomorrow_is_the_day_after_today():
    delta = C._parse_dt("tomorrow", CARACAS) - C._parse_dt("today", CARACAS)
    assert delta.days == 1


def test_now_is_expressed_in_the_requested_zone():
    assert C._parse_dt("now", "Asia/Tokyo").utcoffset().total_seconds() == 9 * 3600


# --------------------------------------------------------------------------
# Zones are real zones, not fixed offsets
# --------------------------------------------------------------------------


def test_offset_follows_daylight_saving():
    """A fixed-offset shortcut would get one of these two wrong."""
    winter = C._parse_time("2026-01-15T09:00:00", "America/New_York")
    summer = C._parse_time("2026-07-15T09:00:00", "America/New_York")
    assert winter.endswith("-05:00"), winter
    assert summer.endswith("-04:00"), summer


def test_unknown_zone_fails_loudly_and_usefully():
    with pytest.raises(ValueError, match="IANA"):
        C._parse_time("2026-08-18T09:00:00", "Mars/Olympus_Mons")


# --------------------------------------------------------------------------
# Default zone resolution
# --------------------------------------------------------------------------


def test_explicit_env_override_wins(monkeypatch):
    monkeypatch.setenv("GWS_TIME_ZONE", CARACAS)
    assert C._local_tz_name() == CARACAS


def test_tz_env_is_used_when_no_explicit_override(monkeypatch):
    monkeypatch.delenv("GWS_TIME_ZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert C._local_tz_name() == "Asia/Tokyo"


def test_garbage_override_is_ignored_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("GWS_TIME_ZONE", "not a zone")
    monkeypatch.delenv("TZ", raising=False)
    assert C._is_zone(C._local_tz_name())


def test_resolved_default_is_always_a_real_zone():
    assert C._is_zone(C.DEFAULT_TZ)
