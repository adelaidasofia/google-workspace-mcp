"""The three runtime bugs that only bit on Windows, and their guards.

All three were silent. None raised on macOS, none raised in CI, and two of them
did not raise on Windows either -- they just did the wrong thing:

  1. Local time resolved to UTC, so "3pm" was booked at 3pm UTC.
  2. The audit log's directory was never created, so the first gmail_send
     raised FileNotFoundError *after* the mail had gone out.
  3. Attachment filenames went to disk unfiltered, so an ordinary "Q3: notes?"
     could not be saved and "../../x" was not confined to the download folder.

Most of these tests run on every platform on purpose. A guard that only runs on
the machine that already broke is a guard nobody sees go red.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import audit
import calendar_tools as C
import gmail_tools


# --------------------------------------------------------------------------
# 1. Local time zone
# --------------------------------------------------------------------------


def test_a_machine_that_knows_its_zone_does_not_answer_utc(monkeypatch):
    """The regression, stated so it can actually fail.

    The pre-existing test asked only that the default be *a real zone*, and UTC
    is a real zone -- so it stayed green through the entire Windows failure.
    What has to be asserted is that a machine whose zone is knowable is not
    answered with the fallback.
    """
    monkeypatch.delenv("GWS_TIME_ZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)

    knowable = C._tz_from_etc_localtime() or C._tz_from_tzlocal()
    if not knowable:
        pytest.skip("this machine genuinely cannot say what zone it is in")

    resolved = C._local_tz_name()
    assert resolved == knowable
    assert resolved != C.FALLBACK_TZ or knowable == C.FALLBACK_TZ


@pytest.mark.skipif(os.name != "nt", reason="the tier that only Windows needs")
def test_windows_resolves_a_zone_without_etc_localtime(monkeypatch):
    """Windows has no /etc/localtime and names zones its own way.

    Before the tzlocal tier, every Windows install fell through to UTC here.
    """
    monkeypatch.delenv("GWS_TIME_ZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    assert C._tz_from_etc_localtime() is None, "unexpected /etc/localtime on Windows"

    name = C._tz_from_tzlocal()
    assert name, "tzlocal could not name this machine's zone"
    assert C._is_zone(name)


def test_a_naive_time_lands_in_the_local_zone_not_utc(monkeypatch):
    """The consequence, end to end: the offset stamped on a typed-in time."""
    monkeypatch.delenv("GWS_TIME_ZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    zone = C._local_tz_name()
    if zone == "UTC":
        pytest.skip("machine really is on UTC, so there is nothing to tell apart")
    stamped = C._parse_time("2026-08-18T09:00:00", zone)
    assert stamped.startswith("2026-08-18T09:00:00")
    assert not stamped.endswith("+00:00")


def test_the_tzlocal_tier_degrades_instead_of_exploding(monkeypatch):
    """tzlocal is a dependency, but an older checkout must still start."""
    monkeypatch.delenv("GWS_TIME_ZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(C, "_tz_from_tzlocal", lambda: None)
    monkeypatch.setattr(C, "_tz_from_etc_localtime", lambda: None)
    assert C._local_tz_name() == C.FALLBACK_TZ


def test_an_explicit_override_still_beats_the_machine(monkeypatch):
    monkeypatch.setenv("GWS_TIME_ZONE", "Asia/Tokyo")
    assert C._local_tz_name() == "Asia/Tokyo"


# --------------------------------------------------------------------------
# 2. The audit log
# --------------------------------------------------------------------------


def test_audit_creates_its_own_directory(tmp_path, monkeypatch):
    """`open(path, "a")` does not make the parents. On a machine where nothing
    had created ~/.claude/google-workspace-mcp, the first gmail_send raised
    FileNotFoundError after the message was already delivered."""
    target = tmp_path / "never" / "made" / "audit.log"
    monkeypatch.setattr(audit, "LOG_PATH", target)

    audit.record("gmail_send", "account=default to=a@b.c")

    assert target.exists()
    assert "gmail_send" in target.read_text(encoding="utf-8")


def test_audit_writes_utf8_regardless_of_locale(tmp_path, monkeypatch):
    """Python's text mode defaults to the locale encoding, which on Windows is
    a legacy code page. An accented name or an emoji in a subject line -- plain
    ordinary mail -- then raised UnicodeEncodeError, again after the send."""
    target = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "LOG_PATH", target)

    audit.record("gmail_send", "subject='Año nuevo 🎉 — Muñoz'")

    written = target.read_text(encoding="utf-8")
    assert "Año nuevo 🎉 — Muñoz" in written


def test_audit_never_breaks_the_action_it_records(tmp_path, monkeypatch):
    """The log is a record of the send, not a precondition for it. A failure
    here must not turn a delivered message into a raised exception."""
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit.log")
    monkeypatch.setattr(
        audit.pathlib.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("full"))
    )
    audit.record("gmail_send", "detail")  # must not raise


def test_the_tool_modules_share_one_audit_implementation():
    """Three copies of these six lines is how the same two bugs got fixed
    twice and missed once."""
    import docs_tools
    import drive_tools

    assert gmail_tools._audit is audit.record
    assert drive_tools._audit is audit.record
    assert docs_tools._audit is audit.record


# --------------------------------------------------------------------------
# 3. Attachment filenames
# --------------------------------------------------------------------------

FALLBACK = "attachment-abcd1234"


@pytest.mark.parametrize(
    "raw",
    [
        'Q3: results?.pdf',
        'invoice"final".pdf',
        "report|v2.xlsx",
        "notes<draft>.txt",
        "path\\like\\name.doc",
        "why*not.png",
    ],
)
def test_names_windows_rejects_are_made_writable(raw, tmp_path):
    """Every one of these is an ordinary filename on macOS and an OSError on
    NTFS. The attachment came from the sender, so the name is not ours to
    assume anything about."""
    safe = gmail_tools._safe_filename(raw, FALLBACK)
    assert not set(safe) & set('<>:"/\\|?*')
    # The real proof: it can actually be written on this platform.
    (tmp_path / safe).write_bytes(b"x")
    assert (tmp_path / safe).exists()


@pytest.mark.parametrize(
    "raw",
    [
        "../../.ssh/authorized_keys",
        "..\\..\\Windows\\System32\\evil.dll",
        "/etc/passwd",
        "C:\\Windows\\system.ini",
    ],
)
def test_a_sender_cannot_escape_the_download_directory(raw, tmp_path):
    safe = gmail_tools._safe_filename(raw, FALLBACK)
    resolved = (tmp_path / safe).resolve()
    assert resolved.parent == tmp_path.resolve(), f"{raw!r} escaped as {safe!r}"


@pytest.mark.parametrize("raw", ["CON", "con.txt", "NUL.pdf", "COM1", "lpt9.doc"])
def test_windows_device_names_are_defused(raw, tmp_path):
    """CON, NUL and the COM/LPT ports are devices at every directory level:
    opening CON.txt talks to the console rather than creating a file."""
    safe = gmail_tools._safe_filename(raw, FALLBACK)
    assert safe.split(".", 1)[0].upper() not in gmail_tools._RESERVED_NAMES
    (tmp_path / safe).write_bytes(b"x")
    assert (tmp_path / safe).read_bytes() == b"x"


def test_a_trailing_dot_is_not_silently_dropped():
    """Win32 strips a trailing dot or space on the way to the filesystem, so
    "report." and "report" quietly become the same file."""
    assert gmail_tools._safe_filename("report.", FALLBACK) == "report"
    assert gmail_tools._safe_filename("report ", FALLBACK) == "report"


def test_an_empty_or_dotted_name_falls_back():
    for raw in ("", "   ", ".", "..", "/", "\\", "///"):
        assert gmail_tools._safe_filename(raw, FALLBACK) == FALLBACK


def test_an_ordinary_name_is_left_alone():
    for raw in ("Q3 report.pdf", "presupuesto-2026.xlsx", "photo.JPEG", ".gitignore"):
        assert gmail_tools._safe_filename(raw, FALLBACK) == raw


def test_a_very_long_name_keeps_its_extension():
    raw = "a" * 400 + ".pdf"
    safe = gmail_tools._safe_filename(raw, FALLBACK)
    assert len(safe.encode()) <= 255
    assert safe.endswith(".pdf")


def test_a_long_non_ascii_name_is_trimmed_by_bytes_not_characters(tmp_path):
    """255 characters of Cyrillic is 510 bytes, which ext4 rejects. Trimming
    must also not split a character in half and produce mojibake."""
    raw = "щ" * 400 + ".pdf"
    safe = gmail_tools._safe_filename(raw, FALLBACK)
    assert len(safe.encode()) <= 255
    assert safe.endswith(".pdf")
    assert "�" not in safe
    (tmp_path / safe).write_bytes(b"x")


def test_attachment_save_writes_through_the_sanitizer(monkeypatch, tmp_path):
    """The unit above is only worth having if the tool actually calls it."""
    import base64

    class _Att:
        def get(self, **kw):
            return self

        def execute(self):
            return {"data": base64.urlsafe_b64encode(b"payload").decode().rstrip("=")}

    class _Msgs:
        def attachments(self):
            return _Att()

    class _Users:
        def messages(self):
            return _Msgs()

    class _Svc:
        def users(self):
            return _Users()

    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: _Svc())

    result = gmail_tools.attachment_save(
        "MSG1", "ATT12345678", filename="../../Q3: notes?.pdf", dest_dir=str(tmp_path)
    )

    written = Path(result["path"])
    assert written.parent == tmp_path
    assert written.read_bytes() == b"payload"
    assert result["filename"] == written.name
