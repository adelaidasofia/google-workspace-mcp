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

Hermetic: `claude` is a shim on a sealed PATH, the venv interpreter is
pre-made, so nothing here touches the real Claude Code config, pip, or the
network. That is enforced, not hoped for: every installer run gets a throwaway
profile and a PATH that cannot reach a real `claude`, and refuses to start if
one could be reached (see "Keeping the installer away from the developer's
real Claude Code" below).
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


# --------------------------------------------------------------------------
# Keeping the installer away from the developer's real Claude Code
#
# install.ps1 ends by running `claude mcp add ... -s user`. If a real Claude
# Code is the one that answers, that writes a connector into the developer's
# own config, pointing at a pytest temp directory that is gone by the next
# run, and every Claude Code session on the machine then shows it as failing.
# A test that deletes its own fake claude.cmd and runs the installer on the
# machine's real PATH is all it takes.
#
# So two layers, neither of which depends on a test remembering anything:
#   * every run gets a profile (HOME, USERPROFILE, APPDATA, LOCALAPPDATA) and a
#     CLAUDE_CONFIG_DIR inside its own tmp dir, so even a real claude reached
#     by mistake could only write a throwaway config;
#   * every run gets a sealed PATH (the fixture's own dir, the base install of
#     the interpreter running these tests, and the Windows system dirs, nothing
#     else) and first checks that `claude` cannot resolve to anything outside
#     its tmp dir, refusing to start if it can.
#
# Neither layer can be exercised from a non-Windows host, because the whole
# module is skipped there; the guard's own tests below run on the Windows job.
# --------------------------------------------------------------------------

# The names `claude` can go by on a PATH entry: the bare name, the usual
# executable and script extensions, and the .ps1 that npm installs next to
# claude.cmd. A bare file is included on purpose: flagging one PowerShell would
# not run is the safe direction for a guard.
_CLAUDE_NAMES = tuple("claude" + ext for ext in ("", ".exe", ".cmd", ".bat", ".com", ".ps1"))
_PROFILE_KEYS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR")


def _throwaway_profile(root: Path) -> dict:
    """Every place a user profile or a Claude config is looked up, all inside `root`."""
    dirs = {
        "HOME": root / "home",
        "USERPROFILE": root / "home",
        "APPDATA": root / "appdata",
        "LOCALAPPDATA": root / "localappdata",
        "CLAUDE_CONFIG_DIR": root / "claude-config",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return {key: str(d) for key, d in dirs.items()}


def _system_dirs() -> list:
    """The few Windows directories a PowerShell run needs on PATH, and no others."""
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return [
        os.path.join(system_root, "System32"),
        system_root,
        os.path.join(system_root, "System32", "Wbem"),
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0"),
    ]


def _sealed_path(box, *front) -> str:
    """The fixture's own dir, the base install of the interpreter running these
    tests (sys.base_prefix), the system dirs.

    sys.base_prefix is there because the installer looks for a python on PATH,
    and because the pre-made venv's python.exe is a bare copy of this
    interpreter's, which may need its original directory on PATH to find its DLL.
    Once the machine's real PATH is gone nothing else supplies either. `front`
    goes first, for a test that needs one more directory ahead of the rest.

    What this PATH does not reliably carry is the `py` launcher. An all-users
    install puts it in the Windows directory, which is here, but a per-user
    install puts it in a directory of its own, which is not. A test that needs a
    launcher brings its own (see `_launcher_stand_in`) and does not depend on
    whether, or where, the machine has one.
    """
    return os.pathsep.join([*(str(p) for p in front), str(box["bin"]), sys.base_prefix, *_system_dirs()])


def _claude_on(path_value: str) -> list:
    """Every file `claude` could resolve to on this PATH."""
    found = []
    for entry in path_value.split(os.pathsep):
        if not entry:
            continue
        for name in _CLAUDE_NAMES:
            candidate = Path(entry) / name
            if candidate.is_file():
                found.append(candidate)
    return found


def _assert_nothing_real_is_reachable(env: dict, root: Path, *, shim_expected: bool) -> None:
    """Refuse to run the installer unless it can only touch things inside `root`.

    `claude` must resolve to nothing at all (`shim_expected=False`: a test that
    needs `claude` to be absent from PATH also depends on this, because a PATH
    that quietly still found one would pass it for the wrong reason), or to
    shims inside `root`. The profile directories and CLAUDE_CONFIG_DIR must point
    inside `root` as well, and so must CLAUDE_CODE_EXECPATH if it is set:
    install.ps1 does not read that variable today, but a guard that stays the
    same as the POSIX one also covers the day it does.
    """
    root = root.resolve()
    found = _claude_on(env.get("PATH", ""))
    if found:
        assert shim_expected, f"expected no claude on this PATH, but it resolves to {found[0]}"
        for candidate in found:
            assert candidate.resolve().is_relative_to(root), (
                f"a real claude is reachable on the fixture PATH: {candidate}. install.ps1 would "
                f"run `claude mcp add ... -s user` against it for real. PATH={env.get('PATH')}"
            )
    for key in _PROFILE_KEYS:
        assert Path(env[key]).resolve().is_relative_to(root), (
            f"{key} must point inside this test's own tmp dir, got {env[key]!r}"
        )
    execpath = env.get("CLAUDE_CODE_EXECPATH")
    if execpath:
        assert Path(execpath).resolve().is_relative_to(root), (
            f"CLAUDE_CODE_EXECPATH must point inside this test's own tmp dir, got {execpath!r}"
        )


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
    # The installer only checks that git exists, and the PATH is sealed, so the
    # machine's real one is not there to be found.
    _write(bindir / "git.cmd", "@ECHO off\nEXIT /B 0\n")

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


def _installer_env(box, **env_overrides) -> dict:
    """The environment one installer run gets: the machine's own, minus anything
    that could point it at a real Claude Code, plus a sealed PATH and a profile
    that lives inside the test's tmp dir."""
    env = dict(os.environ)
    # Popped so the host's own values -- this suite may itself be running inside
    # a Claude Code session -- can never leak into a run that means to control
    # them explicitly.
    for key in ("GWS_PYTHON", "CLAUDE_CODE_EXECPATH"):
        env.pop(key, None)
    env["PATH"] = _sealed_path(box)
    env.update(_throwaway_profile(box["tmp"]))
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
    return env


def _run(box, *, shim_expected=True, **env_overrides):
    env = _installer_env(box, **env_overrides)
    _assert_nothing_real_is_reachable(env, box["tmp"], shim_expected=shim_expected)
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
    # shim_expected=False: the guard itself checks that nothing on the sealed
    # PATH can answer to `claude`, so a real one cannot be the thing that runs.
    proc, calls = _run(box, shim_expected=False)
    assert proc.returncode != 0
    assert "mcp\tadd" not in calls
    # It has to fail for this reason and no other. With a sealed PATH, a missing
    # python or git would also end the run with a non-zero exit and nothing
    # registered, and this test would pass without having tested anything.
    assert "Claude Code is not installed" in proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# Windows-only traps
# --------------------------------------------------------------------------


def _launcher_stand_in(box) -> None:
    """Put a `py` in the fixture's own dir that answers the way the Python
    launcher does for `py -3`: it drops the version selector and runs the
    interpreter that is running these tests, with everything else it was given.

    The installer asks the launcher before it tries any name, and with the Store
    placeholders in front of `python` and `python3` the launcher is how it gets
    past them to a real interpreter. The sealed PATH keeps the Windows directory,
    where an all-users install puts the launcher, and drops the directory a
    per-user install uses. So a test that depends on one would pass or fail by
    where the machine happens to have it. This one is the fixture's own, and it
    comes ahead of any real one on the PATH.
    """
    forwarder = box["tmp"] / "py_stand_in.py"
    forwarder.write_text(
        "import subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "if args and args[0].startswith('-3'):\n"
        "    args = args[1:]\n"
        "sys.exit(subprocess.run([sys.executable] + args).returncode)\n",
        encoding="utf-8",
    )
    # Shaped like claude.cmd above: a .cmd forwarding its raw %* command line.
    _write(box["bin"] / "py.cmd", f'@ECHO off\n"{sys.executable}" "{forwarder}" %*\n')


def test_the_store_placeholder_is_not_mistaken_for_python(box, tmp_path):
    """A WindowsApps python.exe exists, resolves, and never runs Python.

    The placeholders here stand in front of `python` and `python3`, so the way to
    a real interpreter is the `py` launcher. This test brings its own
    (`_launcher_stand_in`), and does not rely on the machine having one in a
    directory the sealed PATH keeps."""
    fake_store = tmp_path / "WindowsApps"
    fake_store.mkdir()
    # A stub that behaves like the real one: prints its nag line, exits 9009.
    _write(
        fake_store / "python.cmd",
        "@ECHO off\r\nECHO Python was not found; run without arguments to install"
        " from the Microsoft Store\r\nEXIT /B 9009\r\n",
    )
    shutil.copy(fake_store / "python.cmd", fake_store / "python3.cmd")
    _launcher_stand_in(box)

    proc, calls = _run(box, PATH=_sealed_path(box, fake_store))

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


# --------------------------------------------------------------------------
# The harness itself
#
# The guard that keeps the installer away from a real Claude Code is only worth
# having if it can fail. These show that the environment every run gets passes
# it, that it fails on the things it exists to catch, that a run cannot skip
# it, and that the `claude` the installer launches really does receive the
# throwaway profile.
# --------------------------------------------------------------------------


def test_the_environment_every_run_gets_passes_the_guard(box):
    """With claude.cmd in place the sealed PATH resolves `claude` to the shim,
    and with it gone to nothing: never to whatever the host has installed."""
    _assert_nothing_real_is_reachable(_installer_env(box), box["tmp"], shim_expected=True)

    (box["bin"] / "claude.cmd").unlink()
    _assert_nothing_real_is_reachable(_installer_env(box), box["tmp"], shim_expected=False)


@pytest.mark.parametrize("name", _CLAUDE_NAMES)
def test_the_guard_fails_when_a_real_claude_is_reachable(tmp_path, name):
    """Negative control. On a machine with no `claude` at all (CI) every test
    above passes with the guard deleted, so the guard has to be shown failing,
    for each way `claude` can turn up on a Windows PATH."""
    root = tmp_path / "fixture"
    root.mkdir()
    host = tmp_path / "host-install"  # stands in for wherever a real claude lives
    host.mkdir()
    (host / name).write_text("stand-in\n")
    env = {"PATH": str(host), **_throwaway_profile(root)}

    with pytest.raises(AssertionError, match="real claude is reachable"):
        _assert_nothing_real_is_reachable(env, root, shim_expected=True)
    with pytest.raises(AssertionError, match="expected no claude"):
        _assert_nothing_real_is_reachable(env, root, shim_expected=False)


@pytest.mark.parametrize("key", _PROFILE_KEYS)
def test_the_guard_fails_when_the_profile_would_not_be_a_throwaway(tmp_path, key):
    """Negative control for the other layer: a profile directory or a
    CLAUDE_CONFIG_DIR that points outside the test's own tmp dir is refused."""
    root = tmp_path / "fixture"
    root.mkdir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    env = {"PATH": str(root / "empty"), **_throwaway_profile(root), key: str(elsewhere)}

    with pytest.raises(AssertionError, match=key):
        _assert_nothing_real_is_reachable(env, root, shim_expected=False)


def test_the_guard_fails_when_claude_code_execpath_points_outside_the_tmp_dir(tmp_path):
    root = tmp_path / "fixture"
    root.mkdir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    outside = elsewhere / "claude.exe"
    outside.write_text("stand-in\n")
    inside = root / "claude.exe"
    inside.write_text("stand-in\n")
    env = {"PATH": str(root / "empty"), **_throwaway_profile(root)}

    with pytest.raises(AssertionError, match="CLAUDE_CODE_EXECPATH"):
        _assert_nothing_real_is_reachable({**env, "CLAUDE_CODE_EXECPATH": str(outside)}, root, shim_expected=False)
    _assert_nothing_real_is_reachable({**env, "CLAUDE_CODE_EXECPATH": str(inside)}, root, shim_expected=False)
    # An empty value counts as not set, as it does for the installer.
    _assert_nothing_real_is_reachable({**env, "CLAUDE_CODE_EXECPATH": ""}, root, shim_expected=False)


def test_the_runner_checks_the_guard_before_it_starts_the_installer(box, monkeypatch):
    """The guard protects nothing if a run can skip it, so deleting the guard
    call from `_run` has to turn a test red."""

    class GuardReached(Exception):
        pass

    def tripwire(env, root, *, shim_expected):
        raise GuardReached

    monkeypatch.setattr(sys.modules[__name__], "_assert_nothing_real_is_reachable", tripwire)

    with pytest.raises(GuardReached):
        _run(box)


def test_the_claude_the_installer_runs_only_sees_a_throwaway_profile(box):
    """What matters is not what the harness hands the installer but what the
    `claude` the installer launches actually receives."""
    seen = box["tmp"] / "seen-by-claude.log"
    probe = box["tmp"] / "seen-by-claude.py"
    probe.write_text(
        "import os\n"
        f"with open(r'{seen}', 'a', encoding='utf-8') as f:\n"
        f"    f.write('|'.join(os.environ.get(k, '') for k in {_PROFILE_KEYS!r}) + '\\n')\n",
        encoding="utf-8",
    )
    _write(box["bin"] / "claude.cmd", f'@ECHO off\n"{sys.executable}" "{probe}" %*\n')

    proc, _ = _run(box)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    runs = [line.split("|") for line in seen.read_text(encoding="utf-8").splitlines()]
    assert len(runs) == 2, f"expected the remove and the add, got {runs!r}"
    root = box["tmp"].resolve()
    for values in runs:
        assert len(values) == len(_PROFILE_KEYS), values
        for key, value in zip(_PROFILE_KEYS, values):
            assert Path(value).resolve().is_relative_to(root), f"{key}={value!r}"
