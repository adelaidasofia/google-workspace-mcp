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


# ---------------------------------------------------------------------------
# Interpreter discovery
#
# `python3` is only ever the first match on PATH, and on macOS that is
# /usr/bin/python3 (3.9) even when Homebrew has a 3.14 one directory further
# along. The installer used to stop at that first answer and send people to
# python.org for a Python they already had.
#
# These seal PATH down to a fixed set of shims, so the test decides exactly
# which interpreters exist and the result does not depend on what happens to be
# installed on the machine running it.
# ---------------------------------------------------------------------------

BASE_TOOLS = ("sed", "cat", "rm", "dirname")
BASH = shutil.which("bash")


def _py_shim(path: Path, version: str) -> None:
    """A stand-in interpreter that answers only the two questions install.sh asks."""
    minor = version.split(".")[1]
    _exe(
        path,
        f"""#!/bin/sh
for a in "$@"; do
  case "$a" in
    *'print("%d.%d"%sys.version_info[:2])'*) echo "{version}"; exit 0 ;;
    # The pre-fix installer asked the same question by printing 1/0. Answering
    # both forms keeps this a model of an interpreter rather than of one script,
    # so a failure against either version means the semantics differ.
    *'print(1 if sys.version_info'*) [ {minor} -ge 10 ] && echo 1 || echo 0; exit 0 ;;
    *'version_info[:2] >= (3,10)'*) [ {minor} -ge 10 ] && exit 0 || exit 1 ;;
  esac
done
exit 0
""",
    )


@pytest.fixture
def sealed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(INSTALL_SH, repo / "install.sh")
    (repo / "server.py").write_text("# stub\n")
    (repo / "requirements.txt").write_text("")
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _exe(venv_bin / "python", "#!/bin/sh\nexit 0\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in BASE_TOOLS:
        real = shutil.which(tool)
        if real is None:
            pytest.skip(f"{tool} unavailable, cannot seal PATH")
        (bindir / tool).symlink_to(real)

    log = tmp_path / "calls.log"
    log.touch()
    _exe(bindir / "claude", f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$2" in remove) exit 1 ;; esac\nexit 0\n')
    _exe(bindir / "git", "#!/bin/sh\nexit 0\n")

    return {"repo": repo, "bin": bindir, "log": log}


def _run_sealed(sealed, **extra):
    env = {"PATH": str(sealed["bin"]), "NO_COLOR": "1", "HOME": str(sealed["repo"])}
    env.update({"GWS_CLIENT_ID": ID, "GWS_CLIENT_SECRET": SECRET})
    env.update(extra)
    proc = subprocess.run(
        [BASH, str(sealed["repo"] / "install.sh")],
        capture_output=True, text=True, env=env, input="", timeout=120,
    )
    return proc, sealed["log"].read_text()


def _chosen(proc):
    """The one line where the installer says which interpreter it settled on."""
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("+ python3")]
    assert len(lines) == 1, f"expected one interpreter line, got {lines!r}\n{proc.stdout}"
    return lines[0]


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_versioned_interpreter_is_used_when_python3_is_too_old(sealed):
    """The reported bug: 3.9 first on PATH, a good one available under its own name."""
    _py_shim(sealed["bin"] / "python3", "3.9")
    _py_shim(sealed["bin"] / "python3.12", "3.12")

    proc, calls = _run_sealed(sealed)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3.12" in _chosen(proc)
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_the_newest_qualifying_interpreter_wins(sealed):
    _py_shim(sealed["bin"] / "python3", "3.9")
    for version in ("3.10", "3.11", "3.13"):
        _py_shim(sealed["bin"] / f"python{version}", version)

    proc, _ = _run_sealed(sealed)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3.13" in _chosen(proc)


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_plain_python3_is_kept_when_it_already_qualifies(sealed):
    """Nothing changes for anyone the installer already worked for."""
    _py_shim(sealed["bin"] / "python3", "3.12")
    _py_shim(sealed["bin"] / "python3.14", "3.14")

    proc, _ = _run_sealed(sealed)

    chosen = _chosen(proc)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3.12" in chosen and "3.14" not in chosen


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_everything_too_old_fails_and_says_what_it_found(sealed):
    _py_shim(sealed["bin"] / "python3", "3.9")
    _py_shim(sealed["bin"] / "python3.8", "3.8")

    proc, calls = _run_sealed(sealed)

    assert proc.returncode != 0
    assert "mcp add" not in calls, "must not register against an unusable interpreter"
    assert "3.9" in proc.stderr, f"should report what it found: {proc.stderr}"


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_no_python_at_all_says_so(sealed):
    proc, calls = _run_sealed(sealed)

    assert proc.returncode != 0
    assert "mcp add" not in calls
    assert "not installed" in proc.stderr, proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_explicit_override_is_used(sealed):
    _py_shim(sealed["bin"] / "python3", "3.9")
    elsewhere = sealed["repo"].parent / "private-prefix"
    elsewhere.mkdir()
    _py_shim(elsewhere / "python3", "3.12")

    proc, calls = _run_sealed(sealed, GWS_PYTHON=str(elsewhere / "python3"))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert str(elsewhere) in _chosen(proc)
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_broken_override_fails_instead_of_searching_past_it(sealed):
    """Silently using a different interpreter than the one named is the bug class."""
    _py_shim(sealed["bin"] / "python3", "3.12")

    proc, calls = _run_sealed(sealed, GWS_PYTHON="/nonexistent/python3")

    assert proc.returncode != 0
    assert "mcp add" not in calls
    assert "GWS_PYTHON" in proc.stderr, proc.stderr
