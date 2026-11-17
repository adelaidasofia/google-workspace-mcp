"""Who gmail_reply addresses, against a mocked googleapiclient service chain.

Replying to someone else's message goes back to its From. Replying to a
message this mailbox sent -- a follow-up on your own mail, the last message in
most threads that are waiting on an answer -- used to go back to its From too,
which is you: the follow-up landed in your own inbox and nobody else saw it.
Gmail marks every message the mailbox sent, aliases included, with the SENT
label, so that is the signal; the reply goes to the original's To, which is
what Gmail's own Reply button does.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.policy import default
from unittest.mock import MagicMock

import gmail_tools

ME = "Me <me@example.com>"
THEM = "Seller <seller@example.com>"


def _original(labels, from_hdr, to_hdr, cc_hdr=None):
    headers = [
        {"name": "From", "value": from_hdr},
        {"name": "To", "value": to_hdr},
        {"name": "Subject", "value": "123 Main St"},
        {"name": "Message-ID", "value": "<orig@mail.example.com>"},
    ]
    if cc_hdr:
        headers.append({"name": "Cc", "value": cc_hdr})
    return {"id": "MSG1", "threadId": "T1", "labelIds": labels, "payload": {"headers": headers}}


def _mock_service(original):
    svc = MagicMock()
    msgs = svc.users.return_value.messages.return_value
    msgs.get.return_value.execute.return_value = original
    msgs.send.return_value.execute.return_value = {"id": "NEW", "threadId": "T1"}
    return svc


def _patch(monkeypatch, original):
    svc = _mock_service(original)
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)
    return svc


def test_reply_to_a_received_message_goes_to_its_sender(monkeypatch):
    _patch(monkeypatch, _original(["INBOX", "UNREAD"], THEM, ME))

    out = gmail_tools.reply("MSG1", "Thanks", dry_run=True)

    assert out["to"] == [THEM]


def test_reply_to_my_own_sent_message_goes_to_its_recipients_not_me(monkeypatch):
    # The regression: this used to return [ME].
    _patch(monkeypatch, _original(["SENT"], ME, THEM))

    out = gmail_tools.reply("MSG1", "Just following up", dry_run=True)

    assert out["to"] == [THEM]


def test_reply_to_my_own_message_with_several_recipients_keeps_them_all(monkeypatch):
    _patch(monkeypatch, _original(["SENT"], ME, "a@example.com, b@example.com"))

    out = gmail_tools.reply("MSG1", "Following up", dry_run=True)

    assert out["to"] == ["a@example.com", "b@example.com"]


def test_reply_all_to_my_own_message_does_not_add_me(monkeypatch):
    _patch(monkeypatch, _original(["SENT"], ME, THEM, cc_hdr="partner@example.com"))

    out = gmail_tools.reply("MSG1", "Following up", reply_all=True, dry_run=True)

    assert out["to"] == [THEM]
    assert ME not in out["to"]


def test_sent_follow_up_goes_out_threaded_to_the_recipient(monkeypatch):
    svc = _patch(monkeypatch, _original(["SENT"], ME, THEM))
    monkeypatch.setattr(gmail_tools, "_audit", lambda *a, **k: None)

    gmail_tools.reply("MSG1", "Just following up")

    sent_body = svc.users.return_value.messages.return_value.send.call_args.kwargs["body"]
    msg = message_from_bytes(base64.urlsafe_b64decode(sent_body["raw"]), policy=default)
    assert sent_body["threadId"] == "T1"
    assert msg["To"] == THEM
    assert msg["In-Reply-To"] == "<orig@mail.example.com>"
    assert msg["Subject"] == "Re: 123 Main St"
