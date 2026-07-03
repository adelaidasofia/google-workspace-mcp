"""OAuth + multi-account token management for google-workspace-mcp.

Refresh tokens live in one of two backends (see _token_file): the OS keyring
(default — service name "google-workspace-mcp" / KEYRING_SERVICE, encrypted at
rest), or a chmod-600 JSON file when GWS_TOKEN_FILE is set or tokens.json
exists next to this module. The file backend avoids the macOS Keychain's
per-app authorization prompts, which never persist on an ad-hoc-signed
interpreter (e.g. a uv-managed Python). Tokens are keyed by account email.
client_secret.json (the OAuth app identifier, not a user secret) sits on disk
at CLIENT_SECRET_PATH and is gitignored; tokens.json must be gitignored too.

Design: every Gmail/Calendar call takes an `account` email. We look up that
account's refresh token via the token store, rebuild Credentials, auto-refresh
if the access token expired, and hand back a googleapiclient service object.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable

_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_mu = threading.Lock()


def _get_refresh_lock(email: str) -> threading.Lock:
    with _refresh_locks_mu:
        if email not in _refresh_locks:
            _refresh_locks[email] = threading.Lock()
        return _refresh_locks[email]


# In-process credential cache, keyed by email. Populated on first use of an
# account in this process; later calls reuse the cached Credentials and refresh
# in place when the access token expires. This keeps the keychain read to at
# most once per account per process — on macOS each keychain read can pop an
# authorization prompt, so reading per-call produced a prompt storm.
_creds_cache: dict[str, "Credentials"] = {}

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = Path(__file__).parent
CLIENT_SECRET_PATH = BASE_DIR / "client_secret.json"
ACCOUNTS_INDEX_PATH = BASE_DIR / "accounts_index.json"

KEYRING_SERVICE = "google-workspace-mcp"

# Least-privilege scopes, audited against the actual tool call surface (each maps
# to a real tool; see test_scopes.py). Deliberately NOT requested, to keep
# corporate admin-consent an easy yes:
#   - https://mail.google.com/ (full mailbox incl. IMAP/SMTP + permanent delete) —
#     gmail.modify + gmail.send cover every Gmail tool (read, label, archive, trash, send).
#   - drive.file (app-created files only) would be TOO narrow — the Drive tools
#     browse + manage the user's EXISTING files + permissions, which requires full drive.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",       # read + batchModify labels, archive, trash
    "https://www.googleapis.com/auth/gmail.send",          # send / reply / draft-send
    "https://www.googleapis.com/auth/gmail.settings.basic",# list send-as aliases (settings().sendAs().list) — the ONLY settings use
    "https://www.googleapis.com/auth/calendar",            # events read/write
    "https://www.googleapis.com/auth/drive",               # list/export existing files + manage permissions (drive.file insufficient)
    "https://www.googleapis.com/auth/documents",           # Google Docs read/write
    "https://www.googleapis.com/auth/spreadsheets",        # Google Sheets read/write
    "https://www.googleapis.com/auth/userinfo.email",      # account email claim (identity)
    "openid",                                              # OIDC id_token
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


# --- Token storage backend ------------------------------------------------
# Default: the OS keyring (cross-platform, encrypted at rest). Opt-in: a
# chmod-600 JSON file ({email: refresh_token}). File mode skips the macOS
# Keychain entirely, so there are no per-app ACL / partition-list prompts —
# the failure mode when the server runs on an ad-hoc-signed interpreter whose
# signature can't anchor a persistent "Always Allow". File mode is active when
# GWS_TOKEN_FILE is set OR tokens.json exists next to this module. Keep the
# file gitignored.
TOKEN_FILE_ENV = "GWS_TOKEN_FILE"
DEFAULT_TOKEN_FILE = BASE_DIR / "tokens.json"


def _token_file() -> "Path | None":
    env = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    if DEFAULT_TOKEN_FILE.exists():
        return DEFAULT_TOKEN_FILE
    return None


def _file_tokens() -> dict:
    p = _token_file()
    if p is None or not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _file_write(tokens: dict) -> None:
    p = _token_file() or DEFAULT_TOKEN_FILE
    p.write_text(json.dumps(tokens, indent=2))
    try:
        p.chmod(0o600)
    except OSError:
        pass


def _store_get(email: str) -> "str | None":
    if _token_file() is not None:
        return _file_tokens().get(email)
    return keyring.get_password(KEYRING_SERVICE, email)


def _store_set(email: str, refresh_token: str) -> None:
    if _token_file() is not None:
        tokens = _file_tokens()
        tokens[email] = refresh_token
        _file_write(tokens)
    else:
        keyring.set_password(KEYRING_SERVICE, email, refresh_token)


def _store_delete(email: str) -> None:
    if _token_file() is not None:
        tokens = _file_tokens()
        if tokens.pop(email, None) is not None:
            _file_write(tokens)
    else:
        try:
            keyring.delete_password(KEYRING_SERVICE, email)
        except keyring.errors.PasswordDeleteError:
            pass


def list_accounts() -> list[str]:
    """Return all account emails from the on-disk index.

    The index (written by add_account / removed by remove_account) is the source
    of truth. We deliberately do NOT read each token from the keychain here:
    doing so decrypts every secret just to enumerate, which on macOS triggers a
    Keychain authorization prompt per item per process. Token presence is
    verified lazily by _credentials_for(), which fails loud if a token is gone.
    """
    return _load_index()


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

    _store_set(email, creds.refresh_token or "")
    _creds_cache.pop(email, None)  # drop any stale cached creds for a re-auth
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
    _store_delete(email)
    index = [e for e in _load_index() if e != email]
    _save_index(index)
    _creds_cache.pop(email, None)
    return {"email": email, "status": "removed"}


def _credentials_for(email: str) -> Credentials:
    _check_client_secret()
    with _get_refresh_lock(email):
        # Reuse cached credentials when possible to avoid a keychain read (and
        # its macOS prompt) on every call. Refresh in place on expiry — the
        # refresh uses the in-memory refresh_token, not the keychain.
        cached = _creds_cache.get(email)
        if cached is not None:
            if cached.valid:
                return cached
            if cached.expired and cached.refresh_token:
                cached.refresh(Request())
                return cached

        refresh_token = _store_get(email)
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
        _creds_cache[email] = creds
        return creds


def service(api: str, version: str, account: str | None = None):
    """Return an authenticated googleapiclient service for the given account."""
    email = _resolve(account)
    creds = _credentials_for(email)
    return build(api, version, credentials=creds, cache_discovery=False)
