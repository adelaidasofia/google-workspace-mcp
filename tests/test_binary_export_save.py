"""save_to on docs_export and drive_read_file, against a mocked Drive service.

A binary export (a Doc as PDF or .docx, an uploaded PDF) cannot come back as
text, so both tools used to return a byte count and nothing else: the bytes
were fetched and then dropped. With save_to they are written to that path,
parent folders created, and the path comes back instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import docs_tools
import drive_tools

PDF = b"%PDF-1.4\n\xe2\xe3\xcf\xd3 not utf-8"


# --------------------------------------------------------------------------
# docs_export
# --------------------------------------------------------------------------


def _docs_drive(monkeypatch, data):
    svc = MagicMock()
    svc.files.return_value.export_media.return_value.execute.return_value = data
    monkeypatch.setattr(docs_tools, "service", lambda *a, **k: svc)
    return svc


def test_docs_export_pdf_without_save_to_is_still_a_byte_count(monkeypatch):
    _docs_drive(monkeypatch, PDF)

    out = docs_tools.export("DOC1", mime="application/pdf")

    assert out["content"] is None
    assert out["bytes"] == len(PDF)


def test_docs_export_pdf_with_save_to_writes_the_file(monkeypatch, tmp_path):
    _docs_drive(monkeypatch, PDF)
    dest = tmp_path / "exports" / "offer.pdf"

    out = docs_tools.export("DOC1", mime="application/pdf", save_to=str(dest))

    assert dest.read_bytes() == PDF
    assert out == {"id": "DOC1", "mime": "application/pdf", "path": str(dest), "bytes": len(PDF)}


def test_docs_export_text_with_save_to_writes_it_too(monkeypatch, tmp_path):
    _docs_drive(monkeypatch, b"# Title\n")
    dest = tmp_path / "doc.md"

    out = docs_tools.export("DOC1", save_to=str(dest))

    assert dest.read_bytes() == b"# Title\n"
    assert "content" not in out


# --------------------------------------------------------------------------
# drive_read_file
# --------------------------------------------------------------------------


META = {"id": "F1", "name": "county-record.pdf", "mimeType": "application/pdf"}


class _FakeDownloader:
    """Stands in for MediaIoBaseDownload: one chunk, the whole payload."""

    payload = b""

    def __init__(self, buf, request):
        self._buf = buf

    def next_chunk(self):
        self._buf.write(self.payload)
        return None, True


def _drive(monkeypatch, payload):
    svc = MagicMock()
    svc.files.return_value.get.return_value.execute.return_value = META
    monkeypatch.setattr(drive_tools, "service", lambda *a, **k: svc)
    monkeypatch.setattr(_FakeDownloader, "payload", payload)
    monkeypatch.setattr(drive_tools, "MediaIoBaseDownload", _FakeDownloader)
    return svc


def test_read_file_pdf_without_save_to_is_still_a_placeholder(monkeypatch):
    _drive(monkeypatch, PDF)

    out = drive_tools.read_file("F1", include_content=True)

    assert out["content"].startswith("(binary,")
    assert out["bytes"] == len(PDF)


def test_read_file_with_save_to_writes_the_bytes(monkeypatch, tmp_path):
    _drive(monkeypatch, PDF)
    dest = tmp_path / "records" / "county-record.pdf"

    out = drive_tools.read_file("F1", save_to=str(dest))

    assert dest.read_bytes() == PDF
    assert out["path"] == str(dest)
    assert out["bytes"] == len(PDF)
    assert out["name"] == "county-record.pdf"
    assert "content" not in out


def test_read_file_metadata_only_does_not_download(monkeypatch):
    svc = _drive(monkeypatch, PDF)

    out = drive_tools.read_file("F1")

    assert "path" not in out and "content" not in out
    svc.files.return_value.get_media.assert_not_called()
