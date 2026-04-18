"""OAuth + multi-account token management for google-workspace-mcp.

Refresh tokens live in the macOS Keychain under service name "google-workspace-mcp" (KEYRING_SERVICE),
keyed by the account email. client_secret.json (the OAuth app identifier, not
a user secret) sits on disk at CLIENT_SECRET_PATH and is gitignored.

Design: every Gmail/Calendar call takes an `account` email. We look up that
account's refresh token in the keychain, rebuild Credentials, auto-refresh if
the access token expired, and hand back a googleapiclient service object.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).parent
CLIENT_SECRET_PATH = BASE_DIR / "client_secret.json"
ACCOUNTS_INDEX_PATH = BASE_DIR / "accounts_index.json"

KEYRING_SERVICE = "google-workspace-mcp"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

DEFAULT_ACCOUNT_ENV = "GWS_DEFAULT_ACCOUNT"


class AccountError(Exception):
    """Raised when an account is missing, unauthorized, or can't be refreshed."""


def _load_index() -> list[str]:
    if not ACCOUNTS_INDEX_PATH.exists():
        return []
    try:
        return json.loads(ACCOUNTS_INDEX_PATH.read_text())
    except json.JSONDecodeError:
        return []


def _save_index(emails: Iterable[str]) -> None:
    ACCOUNTS_INDEX_PATH.write_text(json.dumps(sorted(set(emails)), indent=2))


def list_accounts() -> list[str]:
    """Return all account emails that currently have a refresh token in the keychain."""
    return [e for e in _load_index() if keyring.get_password(KEYRING_SERVICE, e)]


def default_account() -> str:
    """Return the default account: env override, else first account in index."""
    env_default = os.environ.get(DEFAULT_ACCOUNT_ENV, "").strip()
    if env_default:
        return env_default
    accounts = list_accounts()
    if not accounts:
        raise AccountError(
            "No accounts configured. Run gws_account_add first, or see SETUP.md."
        )
    return accounts[0]


def _resolve(account: str | None) -> str:
    return (account or "").strip() or default_account()


def _check_client_secret() -> None:
    if not CLIENT_SECRET_PATH.exists():
        raise AccountError(
            f"Missing {CLIENT_SECRET_PATH}. Follow SETUP.md to create an OAuth "
            "client in Google Cloud Console and drop the JSON here."
        )


def add_account(port: int = 0) -> dict:
    """Launch OAuth browser flow, store refresh token, return the email + scopes.

    Call this once per account. Uses localhost redirect on a random port.
    """
    _check_client_secret()
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=True, prompt="consent")

    userinfo = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    email = userinfo.get("email", "").lower()
    if not email:
        raise AccountError("OAuth succeeded but userinfo didn't return an email.")

    keyring.set_password(KEYRING_SERVICE, email, creds.refresh_token or "")
    if not creds.refresh_token:
        raise AccountError(
            "Google didn't return a refresh_token. Revoke access at "
            "https://myaccount.google.com/permissions and retry — the "
            "`prompt=consent` flag should force a fresh token."
        )

    index = _load_index()
    if email not in index:
        index.append(email)
        _save_index(index)

    return {"email": email, "scopes": list(creds.scopes or []), "status": "added"}


def remove_account(email: str) -> dict:
    email = email.lower().strip()
    try:
        keyring.delete_password(KEYRING_SERVICE, email)
    except keyring.errors.PasswordDeleteError:
        pass
    index = [e for e in _load_index() if e != email]
    _save_index(index)
    return {"email": email, "status": "removed"}


def _credentials_for(email: str) -> Credentials:
    _check_client_secret()
    refresh_token = keyring.get_password(KEYRING_SERVICE, email)
    if not refresh_token:
        raise AccountError(
            f"No refresh token for {email}. Run gws_account_add to authorize it."
        )

    client_cfg = json.loads(CLIENT_SECRET_PATH.read_text())
    installed = client_cfg.get("installed") or client_cfg.get("web") or {}
    client_id = installed.get("client_id")
    client_secret = installed.get("client_secret")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def service(api: str, version: str, account: str | None = None):
    """Return an authenticated googleapiclient service for the given account."""
    email = _resolve(account)
    creds = _credentials_for(email)
    return build(api, version, credentials=creds, cache_discovery=False)
