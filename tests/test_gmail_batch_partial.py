"""gmail_tools.search must not present a partial batch as a complete answer.

Gmail's batch endpoint reports per-inner-request failures through the callback's
`exception` argument. The previous `_collect` kept only successes and dropped
exceptions on the floor, so `search` returned a short list with nothing marking
it short. Observed 2026-09-08: an INBOX query for 45 IDs returned 27 summaries
and the caller had no way to know 18 were missing -- a wrong count that reads
exactly like a right one.

These tests pin both directions: a fully successful batch still returns every
summary, and a batch with any failed inner request raises instead of truncating.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import gmail_tools


def _service_for(ids, failures=()):
    """Fake the svc chain. The batch object replays the callback per added id,
    handing an exception for every id listed in `failures`."""
    svc = MagicMock()
    svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": i} for i in ids]
    }
    svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": []
    }

    class _Batch:
        def __init__(self, callback):
            self._callback = callback
            self._ids = []

        def add(self, _request, request_id):
            self._ids.append(request_id)

        def execute(self):
            for mid in self._ids:
                if mid in failures:
                    self._callback(mid, None, RuntimeError("backend said no"))
                else:
                    self._callback(
                        mid,
                        {"id": mid, "labelIds": [], "payload": {"headers": []}},
                        None,
                    )

    svc.new_batch_http_request.side_effect = lambda callback: _Batch(callback)
    return svc


def _wire(monkeypatch, svc):
    monkeypatch.setattr(gmail_tools, "service", lambda *a, **k: svc)
    monkeypatch.setattr(gmail_tools, "_label_map", lambda _svc: {})


def test_complete_batch_returns_every_summary(monkeypatch):
    ids = [f"m{i}" for i in range(5)]
    _wire(monkeypatch, _service_for(ids))
    result = gmail_tools.search(query="in:inbox", limit=5)
    assert len(result) == 5


def test_partial_batch_raises_instead_of_truncating(monkeypatch):
    ids = [f"m{i}" for i in range(5)]
    _wire(monkeypatch, _service_for(ids, failures={"m1", "m3"}))

    with pytest.raises(RuntimeError) as excinfo:
        gmail_tools.search(query="in:inbox", limit=5)

    message = str(excinfo.value)
    # The count is the part a caller acts on, so it must be in the message.
    assert "3 of 5" in message
    assert "2 missing" in message
    # And the reason travels with it, not just the fact of absence.
    assert "backend said no" in message
