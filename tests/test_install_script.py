"""install.sh is the path every new install takes, so it needs a gate.

Hermetic: `claude` and the venv interpreter are shims on PATH, so nothing here
touches the real Claude Code config or the network.

Three things are worth pinning, and nothing else:
  1. valid input produces the exact `claude mcp add` argv (a lost `--` or a
     dropped `-s user` is invisible until a person tries to use it)
  2. bad input registers nothing and exits non-zero (a zero exit would mean
     the installer reported success having registered something unusable)
  3. re-running heals rather than fails

Subprocess calls pass argument lists, never shell strings.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"

ID = "1234567890-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-averyrealisticlookingsecret"

pytestmark = pytest.mark.skipif(not INSTALL_SH.exists(), reason="no install.sh here")


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def box(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(INSTALL_SH, repo / "install.sh")
    (repo / "server.py").write_text("# stub\n")
    (repo / "requirements.txt").write_text("")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()
    # `mcp remove` exits 1 to mimic "not registered yet", which must be tolerated.
    _exe(
        bindir / "claude",
        f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$2" in remove) exit 1 ;; esac\nexit 0\n',
    )
    # A pre-made venv interpreter skips venv creation, pip and the import check,
    # so this runs in milliseconds and installs nothing.
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _exe(venv_bin / "python", "#!/bin/sh\nexit 0\n")

    return {"repo": repo, "bin": bindir, "log": log}


def _run(box, **creds):
    env = dict(os.environ, PATH=f"{box['bin']}{os.pathsep}{os.environ['PATH']}", NO_COLOR="1")
    for key in ("GWS_CLIENT_ID", "GWS_CLIENT_SECRET"):
        env.pop(key, None)
    env.update(creds)
    proc = subprocess.run(
        ["bash", str(box["repo"] / "install.sh")],
        capture_output=True, text=True, env=env, input="", timeout=120,
    )
    return proc, box["log"].read_text()


def test_valid_credentials_register_the_expected_command(box):
    proc, calls = _run(box, GWS_CLIENT_ID=ID, GWS_CLIENT_SECRET=SECRET)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    add = [ln for ln in calls.splitlines() if ln.startswith("mcp add")]
    assert len(add) == 1, f"expected one `mcp add`, got {calls!r}"
    argv = add[0]

    assert "mcp add google-workspace" in argv
    assert "-s user" in argv, "must register at user scope, not just this project"
    assert f"-e GWS_CLIENT_ID={ID}" in argv
    assert f"-e GWS_CLIENT_SECRET={SECRET}" in argv
    assert " -- " in argv, "without `--` the interpreter path parses as a flag"
    assert argv.rstrip().endswith("server.py")


@pytest.mark.parametrize(
    "creds, why",
    [
        ({"GWS_CLIENT_ID": ID, "GWS_CLIENT_SECRET": ID}, "same value in both fields"),
        ({"GWS_CLIENT_ID": ID}, "secret missing"),
        ({"GWS_CLIENT_SECRET": SECRET}, "id missing"),
    ],
)
def test_bad_credentials_register_nothing(box, creds, why):
    proc, calls = _run(box, **creds)
    assert proc.returncode != 0, f"{why}: should have failed loudly"
    assert "mcp add" not in calls, f"{why}: must not register an unusable client"


def test_rerunning_heals_instead_of_failing(box):
    creds = {"GWS_CLIENT_ID": ID, "GWS_CLIENT_SECRET": SECRET}
    _run(box, **creds)
    proc, calls = _run(box, **creds)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls.count("mcp remove google-workspace") == 2
    assert calls.count("mcp add google-workspace") == 2
