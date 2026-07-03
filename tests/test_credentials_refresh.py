"""Expired refresh token -> friendly, actionable AccountError (MYC-2602).

The 30X shared OAuth app runs in Google "Testing" mode, so external refresh
tokens expire ~7 days after issuance (MYC-2581 -- the load-bearing drive +
gmail.modify scopes are restricted, so this cannot be de-scoped away). On the
next call after expiry, google-auth's Credentials.refresh() raises
RefreshError(invalid_grant). Before this fix that error was uncaught: a student
saw a raw stack trace instead of the one-line fix.

These tests lock the friendly path at BOTH refresh sites in
accounts._credentials_for -- the store-read path and the in-process-cache path
-- using a fake refresh that raises RefreshError. No live Google, no keyring.

Same bug CLASS as MYC-413 (expiry -> actionable re-auth) but a different
surface: this is the google-workspace-mcp desktop MCP (RefreshError ->
AccountError, gws_account_add), not the runtime/webapp Pulse.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import accounts  # noqa: E402


def _install_client_secret(tmp_path, monkeypatch) -> None:
    """Point accounts at a throwaway client_secret.json (an OAuth app
    identifier, not a user secret) so _check_client_secret() passes."""
    secret = tmp_path / "client_secret.json"
    secret.write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})
    )
    monkeypatch.setattr(accounts, "CLIENT_SECRET_PATH", secret)


def _raise_invalid_grant(*_args, **_kwargs):
    # Shape mirrors what google-auth raises for an expired/revoked refresh
    # token: first arg the human string, second the parsed OAuth error payload.
    raise RefreshError(
        "invalid_grant: Token has been expired or revoked.",
        {
            "error": "invalid_grant",
            "error_description": "Token has been expired or revoked.",
        },
    )


def test_store_path_expired_token_raises_actionable_account_error(
    tmp_path, monkeypatch
):
    """Day-8 fresh process: no in-memory cache, refresh token read from the
    store, and its refresh() fails with invalid_grant."""
    email = "student@example.com"
    _install_client_secret(tmp_path, monkeypatch)

    # Stored refresh token via the file backend (no keyring prompt in CI).
    token_file = tmp_path / "tokens.json"
    token_file.write_text(json.dumps({email: "dead-refresh-token"}))
    monkeypatch.setenv(accounts.TOKEN_FILE_ENV, str(token_file))
    accounts._creds_cache.pop(email, None)

    # The rebuilt Credentials' refresh() fails like a day-8 expired token.
    monkeypatch.setattr(accounts.Credentials, "refresh", _raise_invalid_grant)

    with pytest.raises(accounts.AccountError) as exc:
        accounts.service("gmail", "v1", account=email)

    # Actionable: names the one command that fixes it -- the whole point of
    # MYC-2602 (a friendly re-auth line, not a raw RefreshError stack trace).
    assert "gws_account_add" in str(exc.value)
    # The dead token is cleared so the next call surfaces the clean re-auth
    # path, not a repeat of the same failing refresh.
    assert email not in json.loads(token_file.read_text())
    assert email not in accounts._creds_cache


def test_cached_path_expired_credentials_raise_actionable_account_error(
    tmp_path, monkeypatch
):
    """Long-lived process: the account was used earlier, so a Credentials
    object sits in the in-process cache. Its access token expired and the
    in-place refresh() fails with invalid_grant (the second refresh site)."""
    email = "cohort@example.com"
    _install_client_secret(tmp_path, monkeypatch)

    # Route the fix's _store_delete cleanup through the file backend so it stays
    # hermetic (no keyring call) and the cleared token is assertable.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(json.dumps({email: "dead-refresh-token"}))
    monkeypatch.setenv(accounts.TOKEN_FILE_ENV, str(token_file))

    class _ExpiredCachedCreds:
        valid = False
        expired = True
        refresh_token = "dead-refresh-token"

        def refresh(self, request):
            _raise_invalid_grant()

    accounts._creds_cache[email] = _ExpiredCachedCreds()

    with pytest.raises(accounts.AccountError) as exc:
        accounts.service("gmail", "v1", account=email)

    assert "gws_account_add" in str(exc.value)
    # Broken cached creds evicted; dead stored token cleared.
    assert email not in accounts._creds_cache
    assert email not in json.loads(token_file.read_text())
