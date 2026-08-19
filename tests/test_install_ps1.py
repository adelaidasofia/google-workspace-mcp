"""install.ps1 is the path every new Windows install takes, so it needs a gate.

The Windows twin of test_install_script.py, pinning the same three things --
correct argv, nothing registered on bad input, re-running heals -- plus the
two failures that are specific to this platform and that nothing else would
catch:

  * The Microsoft Store's python.exe / python3.exe placeholders are real files
    on PATH that never run Python. An installer that trusts the name picks one
    and fails much later, somewhere unrelated.

  * npm installs claude.ps1 next to claude.cmd, and Get-Command prefers the
    .ps1. PowerShell's parameter binder eats a bare `--` before a script sees
    it, so registering through the .ps1 silently drops the separator that says
    where the server command begins. The registration then succeeds, exit 0,
    with the wrong argv -- which is exactly the shape of bug a test has to
    catch, because a person never will.

Hermetic: `claude` is a shim on PATH, the venv interpreter is pre-made, so
nothing here touches the real Claude Code config, pip, or the network.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_PS1 = REPO_ROOT / "install.ps1"

ID = "1234567890-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-averyrealisticlookingsecret"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = [
    pytest.mark.skipif(not INSTALL_PS1.exists(), reason="no install.ps1 here"),
    pytest.mark.skipif(os.name != "nt", reason="Windows installer"),
    pytest.mark.skipif(POWERSHELL is None, reason="no powershell found"),
]


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="ascii", newline="\r\n")


@pytest.fixture
def box(tmp_path):
    """A sealed repo + a fake `claude` that records the argv it is handed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(INSTALL_PS1, repo / "install.ps1")
    (repo / "server.py").write_text("# stub\n")
    (repo / "requirements.txt").write_text("")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    log.touch()

    # The recorder writes one tab-separated line per invocation. `mcp remove`
    # exits 1 to mimic "not registered yet", which must be tolerated.
    recorder = tmp_path / "recorder.py"
    recorder.write_text(
        "import sys\n"
        f"with open(r'{log}', 'a', encoding='utf-8') as f:\n"
        "    f.write('\\t'.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(1 if (len(sys.argv) > 2 and sys.argv[2] == 'remove') else 0)\n",
        encoding="utf-8",
    )
    # Shaped exactly like npm's shim: a .cmd forwarding its raw %* command line.
    _write(bindir / "claude.cmd", f'@ECHO off\n"{sys.executable}" "{recorder}" %*\n')

    _make_stub_venv(repo / ".venv")

    return {"repo": repo, "bin": bindir, "log": log, "tmp": tmp_path}


def _make_stub_venv(venv: Path) -> None:
    """A pre-made venv, so the installer skips creating one.

    Cheaper than `python -m venv` per test and, more to the point, isolated:
    it is a genuine venv (sys.prefix differs from sys.base_prefix), so the pip
    the installer runs cannot reach the real interpreter's site-packages.
    system-site-packages is on only so `python -m pip` resolves without having
    to bootstrap pip into it; combined with PIP_NO_INDEX in _run, the pip steps
    are offline no-ops against an empty requirements.txt.
    """
    base = Path(sys.base_prefix) / "python.exe"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy(base, scripts / "python.exe")
    (venv / "pyvenv.cfg").write_text(
        f"home = {base.parent}\n"
        "include-system-site-packages = true\n"
        f"version = {sys.version_info[0]}.{sys.version_info[1]}.0\n",
        encoding="utf-8",
    )


def _run(box, **env_overrides):
    env = dict(os.environ)
    env["PATH"] = f"{box['bin']}{os.pathsep}{env['PATH']}"
    env.pop("GWS_PYTHON", None)
    env["GWS_CLIENT_ID"] = ID
    env["GWS_CLIENT_SECRET"] = SECRET
    env["NO_COLOR"] = "1"
    # Nothing in this suite may reach PyPI: requirements.txt is empty, so with
    # no index the pip steps are pure no-ops.
    env["PIP_NO_INDEX"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    for k, v in env_overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    proc = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(box["repo"] / "install.ps1"),
        ],
        capture_output=True,
        text=True,
        # PowerShell 5.1 writes to the console in the OEM code page, not UTF-8,
        # so a strict decode raises inside subprocess's reader thread and
        # leaves proc.stdout as None -- which surfaces as a TypeError in the
        # assertion instead of as the installer's actual output.
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    return proc, box["log"].read_text(encoding="utf-8")


def _add_line(calls: str) -> str:
    for line in calls.splitlines():
        if line.startswith("mcp\tadd"):
            return line
    return ""


# --------------------------------------------------------------------------
# The happy path, and the exact argv it must produce
# --------------------------------------------------------------------------


def test_valid_input_registers_the_expected_argv(box):
    proc, calls = _run(box)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    expected = [
        "mcp",
        "add",
        "google-workspace",
        "-s",
        "user",
        "-e",
        f"GWS_CLIENT_ID={ID}",
        "-e",
        f"GWS_CLIENT_SECRET={SECRET}",
        "--",
        str(box["repo"] / ".venv" / "Scripts" / "python.exe"),
        str(box["repo"] / "server.py"),
    ]
    assert _add_line(calls).split("\t") == expected


def test_the_separator_survives(box):
    """`--` is what tells `claude mcp add` where the server command starts.

    PowerShell removes a bare `--` when it binds arguments for a .ps1, so an
    installer that reaches Claude Code through claude.ps1 loses it here and
    still exits 0. Pinned on its own because the failure is invisible.
    """
    _, calls = _run(box)
    assert "\t--\t" in _add_line(calls)


def test_a_repo_path_with_spaces_still_registers_one_argument_each(box, tmp_path):
    """C:\\Users\\Ana Maria\\... is an ordinary Windows path, and a path split
    on its spaces registers a command nobody can run."""
    spaced = tmp_path / "Program Files Clone" / "google workspace mcp"
    spaced.parent.mkdir(parents=True)
    shutil.copytree(box["repo"], spaced)
    box["repo"] = spaced

    proc, calls = _run(box)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    fields = _add_line(calls).split("\t")
    assert fields[-2] == str(spaced / ".venv" / "Scripts" / "python.exe")
    assert fields[-1] == str(spaced / "server.py")


def test_rerunning_removes_before_adding(box):
    """Re-running has to heal a bad value, not fail on "already exists"."""
    _run(box)
    proc, calls = _run(box)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [l for l in calls.splitlines() if l]
    assert lines[0].startswith("mcp\tremove")
    assert lines.count("mcp\tremove\tgoogle-workspace\t-s\tuser") == 2
    assert len([l for l in lines if l.startswith("mcp\tadd")]) == 2


# --------------------------------------------------------------------------
# Bad input registers nothing
# --------------------------------------------------------------------------


def test_half_an_env_pair_registers_nothing(box):
    proc, calls = _run(box, GWS_CLIENT_SECRET=None)
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls


def test_the_id_pasted_into_both_boxes_registers_nothing(box):
    proc, calls = _run(box, GWS_CLIENT_SECRET=ID)
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls


def test_a_missing_server_py_registers_nothing(box):
    (box["repo"] / "server.py").unlink()
    proc, calls = _run(box)
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls


def test_a_broken_python_override_fails_instead_of_searching_past_it(box):
    """Silently using a different interpreter than the one named is the bug class."""
    proc, calls = _run(box, GWS_PYTHON=r"C:\nonexistent\python.exe")
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls
    assert "GWS_PYTHON" in proc.stdout + proc.stderr


def test_no_claude_on_path_registers_nothing(box):
    (box["bin"] / "claude.cmd").unlink()
    proc, calls = _run(box)
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls


# --------------------------------------------------------------------------
# Windows-only traps
# --------------------------------------------------------------------------


def test_the_store_placeholder_is_not_mistaken_for_python(box, tmp_path):
    """A WindowsApps python.exe exists, resolves, and never runs Python."""
    fake_store = tmp_path / "WindowsApps"
    fake_store.mkdir()
    # A stub that behaves like the real one: prints its nag line, exits 9009.
    _write(
        fake_store / "python.cmd",
        "@ECHO off\r\nECHO Python was not found; run without arguments to install"
        " from the Microsoft Store\r\nEXIT /B 9009\r\n",
    )
    shutil.copy(fake_store / "python.cmd", fake_store / "python3.cmd")

    env_path = f"{fake_store}{os.pathsep}{box['bin']}{os.pathsep}{os.environ['PATH']}"
    proc, calls = _run(box, PATH=env_path)

    # It must find the real interpreter behind the placeholder, not stop at it.
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp\tadd" in calls
    assert "WindowsApps" not in _add_line(calls)


def test_a_placeholder_named_outright_is_rejected_not_used(box, tmp_path):
    """The version probe, not just the path check, has to reject a stub.

    Skipping anything under WindowsApps is the cheap guard; it does nothing for
    a placeholder somewhere else, or for a python.exe that is broken rather
    than fake. GWS_PYTHON goes straight past the search, so pointing it at a
    stub exercises the probe on its own: a stub that exits non-zero without
    printing a version must be fatal, never accepted as an interpreter.
    """
    stub = tmp_path / "stub" / "python.cmd"
    _write(
        stub,
        "@ECHO off\r\nECHO Python was not found; run without arguments to install"
        " from the Microsoft Store\r\nEXIT /B 9009\r\n",
    )
    proc, calls = _run(box, GWS_PYTHON=str(stub))
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls


def test_the_powershell_shim_is_stepped_over(box):
    """claude.ps1 sits next to claude.cmd on every npm install of Claude Code.

    Reaching Claude Code through it drops the `--`, so the picker has to prefer
    the .cmd. The .ps1 here records nothing and prints a marker, so using it
    shows up as a missing `mcp add` rather than as a silent wrong argv.
    """
    _write(box["bin"] / "claude.ps1", 'Write-Output "WRONG-SHIM"\r\n')
    proc, calls = _run(box)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WRONG-SHIM" not in proc.stdout
    assert "\t--\t" in _add_line(calls)


def test_the_script_is_pure_ascii():
    """A .ps1 with no BOM is read through the active code page by PowerShell
    5.1, and cp1252 turns an em dash into a character it accepts as a string
    quote -- the file then fails to parse far from the offending line. Staying
    inside ASCII makes that impossible regardless of code page. This runs
    everywhere, including on the Linux CI job.
    """
    raw = INSTALL_PS1.read_bytes()
    offenders = [(i, hex(b)) for i, b in enumerate(raw) if b > 0x7F]
    assert not offenders, f"non-ASCII bytes in install.ps1 at {offenders[:5]}"


def test_the_script_parses():
    """A syntax error in a shipped installer is only visible when someone runs
    it, by which point they are a person with a broken install."""
    proc = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$ErrorActionPreference='Stop';"
            "$t=$null;$e=$null;"
            f"[System.Management.Automation.Language.Parser]::ParseFile('{INSTALL_PS1}',"
            "[ref]$t,[ref]$e) > $null;"
            "if ($e.Count) { $e | ForEach-Object { Write-Output $_.Message }; exit 1 }",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
