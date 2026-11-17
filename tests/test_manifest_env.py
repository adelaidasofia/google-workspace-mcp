"""The desktop-extension manifest hands the OAuth client to the server under
the names the server actually reads.

manifest.json used to pass GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET
while accounts.client_config() reads GWS_CLIENT_ID / GWS_CLIENT_SECRET, so an
install through the .mcpb route collected the client in its config dialog and
then the server never saw it. Pinned against accounts' own constants so a
rename on either side fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import accounts

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


def _env() -> dict[str, str]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["server"]["mcp_config"]["env"]


def test_manifest_passes_the_env_names_accounts_reads():
    assert set(_env()) == {accounts.CLIENT_ID_ENV, accounts.CLIENT_SECRET_ENV}


def test_each_env_var_is_wired_to_its_own_user_config_field():
    env = _env()
    assert env[accounts.CLIENT_ID_ENV] == "${user_config.google_oauth_client_id}"
    assert env[accounts.CLIENT_SECRET_ENV] == "${user_config.google_oauth_client_secret}"
