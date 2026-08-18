"""Gmail attachment tools against a mocked googleapiclient service chain.

No unit test in this repo has exercised gmail_tools before this file — every
existing test targets scopes, credentials, install.sh, or the server import.
That gap is real and stays open; this only covers the two functions added
here.

Gmail's multipart payload nests (mixed > alternative > related), so a walker
that only scans the top-level `parts` list silently misses attachments buried
one or two levels down. test_walk_finds_a_nested_attachment pins that.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import gmail_tools


def _mock_service(get_return=None, attachment_get_return=None):
    """Fake the svc.users().messages()... chain. Each .execute() call needs
    its own return value, so both branches are wired independently."""
    svc = MagicMock()
    svc.users.return_value.messages.return_value.get.return_value.execute.return_value = (
        get_return
    )
    svc.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = (
        attachment_get_return
    )
    return svc


FLAT_MESSAGE = {
    "payload": {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": "aGk="}},
            {
                "filename": "invoice.pdf",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "ATT1", "size": 40231},
            },
        ],
    }
}

NESTED_MESSAGE = {
    "payload": {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": "aGk="}},
                    {
                        "mimeType": "multipart/related",
                        "parts": [
                            {
                                "filename": "logo.png",
                                "mimeType": "image/png",
                                "body": {"attachmentId": "ATT2", "size": 900},
                            }
                        ],
                    },
                ],
            }
        ],
    }
}


def test_attachments_list_finds_a_top_level_attachment(monkeypatch):
    monkeypatch.setattr(
        gmail_tools, "service", lambda *a, **k: _mock_service(get_return=FLAT_MESSAGE)
    )
    out = gmail_tools.attachments_list("MSG1")
    assert out == [
        {"attachment_id": "ATT1", "filename": "invoice.pdf",
         "mime_type": "application/pdf", "size": 40231}
    ]


def test_attachments_list_finds_a_nested_attachment(monkeypatch):
    # Buried two levels under multipart/alternative > multipart/related — a
    # walker that only scans payload["parts"] once would report zero here.
    monkeypatch.setattr(
        gmail_tools, "service", lambda *a, **k: _mock_service(get_return=NESTED_MESSAGE)
    )
    out = gmail_tools.attachments_list("MSG1")
    assert out == [
        {"attachment_id": "ATT2", "filename": "logo.png",
         "mime_type": "image/png", "size": 900}
    ]


def test_attachments_list_returns_empty_for_no_attachments(monkeypatch):
    body_only = {"payload": {"mimeType": "text/plain", "body": {"data": "aGk="}}}
    monkeypatch.setattr(
        gmail_tools, "service", lambda *a, **k: _mock_service(get_return=body_only)
    )
    assert gmail_tools.attachments_list("MSG1") == []


def test_attachment_save_decodes_unpadded_base64url(monkeypatch, tmp_path):
    # 26 bytes base64-encodes to one '=' of padding — picked deliberately so
    # rstrip("=") below actually removes something. A byte length that is a
    # multiple of 3 needs no padding at all, and would pass this test whether
    # or not the re-padding logic in attachment_save works.
    raw = b"%PDF-1.4 fake invoice byte"
    assert len(raw) == 26
    data = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert "=" not in data, "fixture no longer exercises the re-padding path"
    monkeypatch.setattr(
        gmail_tools, "service",
        lambda *a, **k: _mock_service(attachment_get_return={"data": data, "size": len(raw)}),
    )
    result = gmail_tools.attachment_save(
        "MSG1", "ATT1", filename="invoice.pdf", dest_dir=str(tmp_path)
    )
    saved = tmp_path / "invoice.pdf"
    assert saved.read_bytes() == raw
    assert result == {"path": str(saved), "filename": "invoice.pdf", "bytes": len(raw)}


def test_attachment_save_falls_back_to_placeholder_name(monkeypatch, tmp_path):
    raw = b"mystery bytes"
    data = base64.urlsafe_b64encode(raw).decode()
    monkeypatch.setattr(
        gmail_tools, "service",
        lambda *a, **k: _mock_service(attachment_get_return={"data": data}),
    )
    result = gmail_tools.attachment_save("MSG1", "ATT99999999", dest_dir=str(tmp_path))
    assert result["filename"] == "attachment-ATT99999"
    assert (tmp_path / "attachment-ATT99999").read_bytes() == raw
