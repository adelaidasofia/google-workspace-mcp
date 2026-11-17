"""File attachments on gmail_send and gmail_draft, through _build_raw.

Attachments are local paths. Each is read at build time, so a missing file
raises before the API is called, dry_run included. The body keeps its shape
(plain, or plain+HTML alternatives) as the first part of a multipart/mixed
message, and every file follows with a mime type guessed from its extension.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from unittest.mock import MagicMock

import pytest

import gmail_tools

TO = ["seller@example.com"]
SUBJECT = "Your offer"
PDF = b"%PDF-1.4 one-pager"


def _parse(raw: str) -> EmailMessage:
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)


def _pdf(tmp_path, name="one-pager.pdf"):
    path = tmp_path / name
    path.write_bytes(PDF)
    return path


def test_no_attachments_leaves_a_plain_body_as_before():
    raw, _ = gmail_tools._build_raw(TO, SUBJECT, "Hi")

    assert _parse(raw).get_content_type() == "text/plain"


def test_a_pdf_rides_along_with_the_body(tmp_path):
    raw, _ = gmail_tools._build_raw(TO, SUBJECT, "See attached.", attachments=[str(_pdf(tmp_path))])
    msg = _parse(raw)

    assert msg.get_content_type() == "multipart/mixed"
    assert msg.get_body(preferencelist=("plain",)).get_content() == "See attached.\n"
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == "one-pager.pdf"
    assert att.get_content_type() == "application/pdf"
    assert att.get_content() == PDF


def test_html_body_stays_alternatives_with_attachments(tmp_path):
    raw, _ = gmail_tools._build_raw(
        TO, SUBJECT, "<p>See <b>attached</b>.</p>", attachments=[str(_pdf(tmp_path))]
    )
    msg = _parse(raw)

    first = next(msg.iter_parts())
    assert first.get_content_type() == "multipart/alternative"
    assert [a.get_filename() for a in msg.iter_attachments()] == ["one-pager.pdf"]


def test_unknown_extension_goes_as_octet_stream(tmp_path):
    odd = tmp_path / "comps.zzq"
    odd.write_bytes(b"\x00\x01")
    raw, _ = gmail_tools._build_raw(TO, SUBJECT, "Hi", attachments=[str(odd)])

    (att,) = list(_parse(raw).iter_attachments())
    assert att.get_content_type() == "application/octet-stream"
    assert att.get_content() == b"\x00\x01"


def test_missing_attachment_fails_before_anything_is_sent(monkeypatch, tmp_path):
    svc = MagicMock()
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)

    with pytest.raises(FileNotFoundError):
        gmail_tools.send(TO, SUBJECT, "Hi", attachments=[str(tmp_path / "nope.pdf")])
    svc.users.return_value.messages.return_value.send.assert_not_called()


def test_send_dry_run_lists_attachment_names(tmp_path):
    out = gmail_tools.send(TO, SUBJECT, "Hi", attachments=[str(_pdf(tmp_path))], dry_run=True)

    assert out["attachments"] == ["one-pager.pdf"]


def test_send_hands_the_attachment_to_gmail(monkeypatch, tmp_path):
    svc = MagicMock()
    send = svc.users.return_value.messages.return_value.send
    send.return_value.execute.return_value = {"id": "m1", "threadId": "t1"}
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)
    monkeypatch.setattr(gmail_tools, "_audit", lambda *a, **k: None)

    gmail_tools.send(TO, SUBJECT, "Hi", attachments=[str(_pdf(tmp_path))])

    msg = _parse(send.call_args.kwargs["body"]["raw"])
    assert [a.get_filename() for a in msg.iter_attachments()] == ["one-pager.pdf"]


def test_draft_hands_the_attachment_to_gmail(monkeypatch, tmp_path):
    svc = MagicMock()
    create = svc.users.return_value.drafts.return_value.create
    create.return_value.execute.return_value = {"id": "d1", "message": {"id": "m1"}}
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)

    gmail_tools.draft(TO, SUBJECT, "Hi", attachments=[str(_pdf(tmp_path))])

    msg = _parse(create.call_args.kwargs["body"]["message"]["raw"])
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == "one-pager.pdf"
    assert att.get_content() == PDF
