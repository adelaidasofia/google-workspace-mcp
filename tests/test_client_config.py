"""OAuth client resolution: env pair wins, file is the fallback.

The env pair exists so a client can register the connector in one command with
no file on disk. The half-set cases are the ones worth pinning: a typo in one
variable must fail loudly rather than silently authorizing against whatever
client_secret.json happens to be lying next to the module.
"""

import json

import pytest

import accounts


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(accounts.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(accounts.CLIENT_SECRET_ENV, raising=False)


def _write_file(tmp_path, monkeypatch, client_id="file-cid"):
    path = tmp_path / "client_secret.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": client_id,
                    "client_secret": "file-secret",
                    "redirect_uris": ["http://localhost"],
                    "auth_uri": accounts.AUTH_URI,
                    "token_uri": accounts.TOKEN_URI,
                }
            }
        )
    )
    monkeypatch.setattr(accounts, "CLIENT_SECRET_PATH", path)
    return path


def _point_at_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "CLIENT_SECRET_PATH", tmp_path / "absent.json")


def test_env_pair_builds_a_complete_installed_config(tmp_path, monkeypatch):
    _point_at_missing_file(tmp_path, monkeypatch)
    monkeypatch.setenv(accounts.CLIENT_ID_ENV, "env-cid")
    monkeypatch.setenv(accounts.CLIENT_SECRET_ENV, "env-secret")

    installed = accounts.client_config()["installed"]

    assert installed["client_id"] == "env-cid"
    assert installed["client_secret"] == "env-secret"
    # google-auth-oauthlib rejects a config missing either URI, so both must be
    # present even though the caller never supplies them.
    assert installed["auth_uri"] == accounts.AUTH_URI
    assert installed["token_uri"] == accounts.TOKEN_URI
    assert installed["redirect_uris"] == ["http://localhost"]


def test_env_pair_is_accepted_by_the_real_oauth_flow(tmp_path, monkeypatch):
    """Guards the shape, not just the keys: the library validates this dict."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    _point_at_missing_file(tmp_path, monkeypatch)
    monkeypatch.setenv(accounts.CLIENT_ID_ENV, "env-cid")
    monkeypatch.setenv(accounts.CLIENT_SECRET_ENV, "env-secret")

    flow = InstalledAppFlow.from_client_config(accounts.client_config(), accounts.SCOPES)
    url, _ = flow.authorization_url()

    assert url.startswith(accounts.AUTH_URI)
    assert "env-cid" in url


def test_file_is_used_when_env_is_absent(tmp_path, monkeypatch):
    _write_file(tmp_path, monkeypatch)
    assert accounts.client_config()["installed"]["client_id"] == "file-cid"


def test_env_wins_over_file(tmp_path, monkeypatch):
    _write_file(tmp_path, monkeypatch)
    monkeypatch.setenv(accounts.CLIENT_ID_ENV, "env-cid")
    monkeypatch.setenv(accounts.CLIENT_SECRET_ENV, "env-secret")

    assert accounts.client_config()["installed"]["client_id"] == "env-cid"


@pytest.mark.parametrize("present", ["CLIENT_ID_ENV", "CLIENT_SECRET_ENV"])
def test_half_set_env_raises_instead_of_falling_back_to_the_file(
    tmp_path, monkeypatch, present
):
    # The file is present and valid. A half-set env pair must NOT quietly use it:
    # that would authorize against a different client than the caller named.
    _write_file(tmp_path, monkeypatch)
    monkeypatch.setenv(getattr(accounts, present), "only-half")

    with pytest.raises(accounts.AccountError) as excinfo:
        accounts.client_config()

    missing = (
        accounts.CLIENT_SECRET_ENV
        if present == "CLIENT_ID_ENV"
        else accounts.CLIENT_ID_ENV
    )
    assert missing in str(excinfo.value)


def test_blank_env_values_are_treated_as_unset(tmp_path, monkeypatch):
    _write_file(tmp_path, monkeypatch)
    monkeypatch.setenv(accounts.CLIENT_ID_ENV, "   ")
    monkeypatch.setenv(accounts.CLIENT_SECRET_ENV, "")

    assert accounts.client_config()["installed"]["client_id"] == "file-cid"


def test_no_env_and_no_file_names_both_ways_in(tmp_path, monkeypatch):
    _point_at_missing_file(tmp_path, monkeypatch)

    with pytest.raises(accounts.AccountError) as excinfo:
        accounts.client_config()

    message = str(excinfo.value)
    assert accounts.CLIENT_ID_ENV in message
    assert accounts.CLIENT_SECRET_ENV in message
    assert str(accounts.CLIENT_SECRET_PATH) in message


def test_corrupt_file_reports_the_path(tmp_path, monkeypatch):
    path = tmp_path / "client_secret.json"
    path.write_text("{not json")
    monkeypatch.setattr(accounts, "CLIENT_SECRET_PATH", path)

    with pytest.raises(accounts.AccountError, match="not valid JSON"):
        accounts.client_config()
