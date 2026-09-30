"""Scope least-privilege guard — the requested OAuth scopes stay minimal.

No network, no keyring: asserts on the static SCOPES list. Least privilege is a
SEPARATE invariant from "scopes match across surfaces" — this locks it so a
broader scope can't silently creep in. An over-broad scope is the corporate
admin-consent rejection surface for the 30X cohort. See the audit note above
accounts.SCOPES and the shared-brain rule "A Connector Requests Least-Privilege
Scopes, Audited Against Its Real Call Surface Before the App Is Minted" (MYC-2578).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import accounts  # noqa: E402


def test_scopes_are_the_audited_set():
    # Adding or removing a scope must be a deliberate edit here — it forces a
    # re-audit against the actual tool call surface before the change ships.
    assert set(accounts.SCOPES) == {
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.settings.basic",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    }


def test_no_over_broad_gmail_scope():
    # Full-mailbox scope (IMAP/SMTP + permanent delete). gmail.modify + gmail.send
    # cover every Gmail tool; the full scope must never be requested.
    assert "https://mail.google.com/" not in accounts.SCOPES


# The docs are a second surface for the same list: the browser steps paste
# these lines into Google's manual "add scopes" box. A doc that drifts from
# SCOPES fails later as "invalid_scope" on a user's machine, and Google's box
# rejects the shortened ".../auth/x" form the scope picker displays.
DOCS_WITH_SCOPE_LISTS = ["SETUP.md", "GUIDED-SETUP.md"]
SHORTENED_SCOPE_LINE = re.compile(r"^\s*(?:[-*]\s*)?`?\.\.\./auth/\S+`?\s*$")


def _listed_scopes(text: str) -> set[str]:
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("https://www.googleapis.com/auth/") or line.strip() == "openid"
    }


@pytest.mark.parametrize("doc", DOCS_WITH_SCOPE_LISTS)
def test_setup_docs_list_exactly_the_requested_scopes(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert _listed_scopes(text) == set(accounts.SCOPES)
    assert not [line for line in text.splitlines() if SHORTENED_SCOPE_LINE.match(line)]
