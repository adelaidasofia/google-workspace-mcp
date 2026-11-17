"""drive_upload's default name is the file's own name, on every platform.

It used to be `local_path.rsplit("/", 1)[-1]`, which only knows the POSIX
separator. A Windows path such as C:\\Users\\me\\comp.xlsx has no "/", so the
whole path became the Drive file name unless `name` was passed. The fix uses
pathlib, which splits on whatever the running platform's separators are.

The path below comes from tmp_path, so on Windows it is a real backslash path
and this test goes red against the old code; on POSIX it guards the ordinary
case. MediaFileUpload is replaced so nothing is read or sent.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

import drive_tools


@pytest.fixture
def created(monkeypatch):
    svc = MagicMock()
    create = svc.files.return_value.create
    create.return_value.execute.return_value = {"id": "F1", "name": "ignored"}
    monkeypatch.setattr(drive_tools, "service", lambda *a, **k: svc)
    monkeypatch.setattr(drive_tools, "MediaFileUpload", MagicMock())
    return create


def test_default_name_is_the_leaf_of_a_native_path(created, tmp_path):
    local = tmp_path / "comp.xlsx"
    local.write_bytes(b"x")

    drive_tools.upload(str(local))

    assert created.call_args.kwargs["body"]["name"] == "comp.xlsx"


@pytest.mark.skipif(os.name != "nt", reason="backslash is only a separator on Windows")
def test_default_name_of_a_windows_backslash_path(created):
    drive_tools.upload(r"C:\Claude Playground\ALL\OUTPUTS\comp.xlsx")

    assert created.call_args.kwargs["body"]["name"] == "comp.xlsx"


def test_an_explicit_name_still_wins(created, tmp_path):
    drive_tools.upload(str(tmp_path / "comp.xlsx"), name="123 Main St comps")

    assert created.call_args.kwargs["body"]["name"] == "123 Main St comps"
