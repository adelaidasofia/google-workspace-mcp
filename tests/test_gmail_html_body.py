"""How a message body becomes MIME in _build_raw, the one builder behind
gmail_send, gmail_draft and gmail_reply.

A body that opens with HTML markup is sent as multipart/alternative: a
text/plain part (the HTML stripped to text) for clients that cannot render
markup, then the HTML itself, which Gmail renders as a normal email. Sent as
text/plain instead, the markup arrives as literal tags in a hard-wrapped
column.

A plain body is sent exactly as before: one text/plain part, nothing added.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from unittest.mock import MagicMock

import gmail_tools

TO = ["recipient@example.com"]
SUBJECT = "Agenda"
HTML = "<p>Hello <b>there</b>,</p><p>See you at 10.</p>"


def _parse(raw: str) -> EmailMessage:
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=default)


def _build(body: str) -> EmailMessage:
    raw, _ = gmail_tools._build_raw(TO, SUBJECT, body)
    return _parse(raw)


def test_html_body_is_sent_as_plain_and_html_alternatives():
    msg = _build(HTML)

    assert msg.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]


def test_html_part_carries_the_body_verbatim():
    msg = _build(HTML)

    assert msg.get_body(preferencelist=("html",)).get_content() == HTML + "\n"


def test_plain_part_is_the_html_stripped_to_text():
    msg = _build(HTML)

    plain = msg.get_body(preferencelist=("plain",)).get_content()
    assert plain == "Hello there,\n\nSee you at 10.\n"


def test_leading_whitespace_before_the_markup_still_counts_as_html():
    msg = _build("\n  " + HTML)

    assert msg.get_content_type() == "multipart/alternative"


def test_plain_body_is_one_text_plain_part_as_before():
    body = "Hello there,\n\nSee you at 10."
    msg = _build(body)

    assert msg.get_content_type() == "text/plain"
    assert not msg.is_multipart()
    assert msg.get_content() == body + "\n"


def test_plain_body_opening_with_an_angle_bracket_link_stays_plain():
    # A bare "<" is not markup. Read as HTML, this note would lose its link:
    # browsers drop "<https://...>" as an unknown tag, and so does the
    # stripped text/plain part.
    body = "<https://example.com/agenda> has the agenda."
    msg = _build(body)

    assert msg.get_content_type() == "text/plain"
    assert msg.get_content() == body + "\n"


def test_draft_hands_an_html_body_to_gmail_as_alternatives(monkeypatch):
    svc = MagicMock()
    create = svc.users.return_value.drafts.return_value.create
    create.return_value.execute.return_value = {"id": "d1", "message": {"id": "m1"}}
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)

    gmail_tools.draft(to=TO, subject=SUBJECT, body=HTML)

    raw = create.call_args.kwargs["body"]["message"]["raw"]
    assert _parse(raw).get_content_type() == "multipart/alternative"
