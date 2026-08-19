"""The write-audit log, shared by the Gmail, Drive and Docs tools.

One append-only line per destructive or outbound action, so "what did the
assistant actually send / trash / rewrite" has an answer that does not depend
on the chat transcript.

Three things this file exists to get right, each of which was wrong when the
log was three copies of the same six lines:

  1. The directory is created. `open(path, "a")` does not make the parents, so
     on a machine where nothing else had created ~/.claude/google-workspace-mcp
     the very first gmail_send raised FileNotFoundError -- after the mail had
     already gone out.

  2. The encoding is pinned to UTF-8. Python's text mode defaults to the
     locale encoding, which on Windows is a legacy code page (cp1252 on a
     Western install). A subject line with an emoji or an accented name --
     ordinary mail -- then raised UnicodeEncodeError, again after the send.

  3. A failure here never propagates. The audit trail is a record of the
     action, not a precondition for it; a full disk or a read-only home should
     not turn a delivered message into a raised exception the caller reads as
     "it did not send".
"""

from __future__ import annotations

import datetime
import pathlib

LOG_PATH = pathlib.Path.home() / ".claude" / "google-workspace-mcp" / "audit.log"


def record(action: str, detail: str) -> None:
    """Append one tab-separated `timestamp action detail` line. Never raises."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts}\t{action}\t{detail}\n")
    except OSError:
        pass
