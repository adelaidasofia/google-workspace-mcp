"""install.sh is the path every new install takes, so it needs a gate.

Hermetic: `claude` and the venv interpreter are shims on PATH, so nothing here
touches the real Claude Code config or the network. That is enforced, not hoped
for: every installer run gets a throwaway HOME and CLAUDE_CONFIG_DIR, and
refuses to start if a real `claude` could be reached from the PATH it was given
(see "The harness itself" at the bottom).

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
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
BASH = shutil.which("bash")

ID = "1234567890-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-averyrealisticlookingsecret"

pytestmark = [
    pytest.mark.skipif(not INSTALL_SH.exists(), reason="no install.sh here"),
    # Git Bash can run install.sh on Windows, but install.sh now refuses to run
    # there on purpose (it would register a POSIX venv path a native Claude
    # Code cannot use) and points at install.ps1 instead. Exercising it here
    # would only re-test that refusal, which test_install_ps1.py covers from
    # the side that matters.
    pytest.mark.skipif(os.name == "nt", reason="POSIX installer; see test_install_ps1.py"),
]


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# Keeping the installer away from the developer's real Claude Code
#
# install.sh ends by running `claude mcp add ... -s user`. If a real `claude` is
# the one that answers, that writes a connector into the developer's own
# ~/.claude.json, pointing at a pytest temp directory that is gone by the next
# run, and every Claude Code session on the machine then shows it as failing. A
# fixture that forgets to put its own fake `claude` first is all it takes.
#
# So two layers, neither of which depends on a fixture remembering anything:
#   * every run gets a HOME and CLAUDE_CONFIG_DIR inside its own tmp dir, so
#     even a real `claude` reached by mistake could only write a throwaway
#     config;
#   * every run first checks that `claude` cannot resolve to anything outside
#     its tmp dir, and refuses to start if it can.
# ---------------------------------------------------------------------------


def _throwaway_config(root: Path) -> dict:
    """HOME, CLAUDE_CONFIG_DIR and TMPDIR for one installer run, all inside `root`.

    TMPDIR is not about Claude Code: it keeps the installer's own scratch files
    inside the test's tmp dir too, where a test can see whether they are cleaned up.
    """
    home = root / "home"
    config = root / "claude-config"
    tmp = root / "tmp"
    for d in (home, config, tmp):
        d.mkdir(exist_ok=True)
    return {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config), "TMPDIR": str(tmp)}


def _command_found_on(env: dict, name: str) -> str:
    """Where `command -v <name>` lands under `env`: the lookup install.sh makes."""
    probe = subprocess.run(
        [BASH or "bash", "-c", 'command -v "$1"', "bash", name],
        capture_output=True, text=True, env=env, timeout=30,
    )
    return probe.stdout.strip() if probe.returncode == 0 else ""


def _assert_nothing_real_is_reachable(env: dict, root: Path, *, shim_expected: bool) -> None:
    """Refuse to run the installer unless it can only touch things inside `root`.

    `claude` must resolve to nothing at all (`shim_expected=False`: the tests that
    need `claude` to be absent from PATH also depend on this), or to a shim inside
    `root`. HOME and CLAUDE_CONFIG_DIR must point inside `root` as well, and so
    must CLAUDE_CODE_EXECPATH when it is set: it is the installer's second way of
    reaching a binary, and one outside `root` is run just as a PATH one would be.
    """
    root = root.resolve()
    found = _command_found_on(env, "claude")
    if found:
        assert shim_expected, f"expected no claude on this PATH, but it resolves to {found}"
        assert Path(found).resolve().is_relative_to(root), (
            f"a real claude is reachable on the fixture PATH: {found}. install.sh would "
            f"run `claude mcp add ... -s user` against it for real. PATH={env.get('PATH')}"
        )
    for key in ("HOME", "CLAUDE_CONFIG_DIR"):
        assert Path(env[key]).resolve().is_relative_to(root), (
            f"{key} must point inside this test's own tmp dir, got {env[key]!r}"
        )
    execpath = env.get("CLAUDE_CODE_EXECPATH")
    if execpath:
        assert Path(execpath).resolve().is_relative_to(root), (
            f"CLAUDE_CODE_EXECPATH must point inside this test's own tmp dir, got {execpath!r}: "
            f"install.sh runs whatever it names with --version, and as `claude mcp add` if it answers"
        )


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
    # install.sh needs a python3 of 3.10 or newer from PATH, and this fixture only
    # puts its own dir in front of the machine's real PATH. Left alone, the
    # interpreter is whichever python3 the host has first -- on a stock Mac the
    # system 3.9 -- and the tests that must succeed fail while the ones that must
    # fail pass without reaching the check they are about. So python3 is pinned to
    # the interpreter running the tests, which is one this repo supports (3.11+).
    _exe(bindir / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')

    return {"repo": repo, "bin": bindir, "log": log, "root": tmp_path}


def _box_env(box, **creds):
    env = dict(os.environ, PATH=f"{box['bin']}{os.pathsep}{os.environ['PATH']}", NO_COLOR="1")
    # Popped so the host's own values -- this suite may itself be running
    # inside a Claude Code session with CLAUDE_CODE_EXECPATH exported -- can
    # never leak into a test that means to control it explicitly.
    for key in ("GWS_CLIENT_ID", "GWS_CLIENT_SECRET", "CLAUDE_CODE_EXECPATH"):
        env.pop(key, None)
    env.update(_throwaway_config(box["root"]))
    env.update(creds)
    return env


def _default_signals() -> None:
    """Run in the child before it execs: the signals the tests below send must be
    ones the installer can die of. A pytest started under nohup, or as a job its
    parent does not wait for, passes some of them on ignored, and an installer
    that ignores SIGHUP is not one a HUP can be tested on."""
    for name in ("SIGINT", "SIGHUP", "SIGTERM"):
        signal.signal(getattr(signal, name), signal.SIG_DFL)


def _run_until_interrupted(fx, env, signal_name, ready):
    """Start install.sh in a process group of its own, wait until `ready()`, then
    send that whole group `signal_name` and return what came of it.

    The group, and not only the installer, is what a terminal sends Ctrl-C to and
    what a closing window sends HUP to, so the binary the installer is waiting on
    gets the signal too.
    """
    sig = getattr(signal, signal_name)
    proc = subprocess.Popen(
        [BASH or "bash", str(fx["repo"] / "install.sh")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        start_new_session=True, preexec_fn=_default_signals,
    )
    try:
        deadline = time.monotonic() + 60
        while not ready():
            assert proc.poll() is None, "the installer ended before it got to where it was to be interrupted"
            assert time.monotonic() < deadline, "the installer never got to where it was to be interrupted"
            time.sleep(0.02)
        os.killpg(proc.pid, sig)
        out, err = proc.communicate(timeout=30)
    finally:
        # Whatever happened above, nothing of this run may be left running. By now
        # the group is usually gone, which is ProcessLookupError, or on macOS holds
        # only zombies, which is PermissionError: both mean there is nothing to kill.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _status_a_shell_reports(returncode: int) -> int:
    """What `$?` is for a shell that ran the installer: 128 plus the number of the
    signal that killed it, which Python reports as that number made negative."""
    return 128 - returncode if returncode < 0 else returncode


def _run_installer(fx, env, *, shim_expected, stdin="", interrupt=None):
    """The one place install.sh is started: the guard first, then the run.

    Every runner below goes through here, so a run cannot skip the guard by
    forgetting it; test_every_runner_checks_the_guard_... shows that it cannot.
    `stdin` is what the installer is handed on its standard input.

    `interrupt` is `(signal_name, ready)`: instead of waiting for the installer to
    finish, send it that signal once `ready()` is true (see _run_until_interrupted).
    Its standard input is then empty.
    """
    _assert_nothing_real_is_reachable(env, fx["root"], shim_expected=shim_expected)
    if interrupt is not None:
        assert not stdin, "an interrupted run is not handed any standard input"
        proc = _run_until_interrupted(fx, env, *interrupt)
    else:
        proc = subprocess.run(
            [BASH or "bash", str(fx["repo"] / "install.sh")],
            capture_output=True, text=True, env=env, input=stdin, timeout=120,
        )
    return proc, fx["log"].read_text()


def _run(box, **creds):
    return _run_installer(box, _box_env(box, **creds), shim_expected=True)


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
    "creds, why, said",
    [
        ({"GWS_CLIENT_ID": ID, "GWS_CLIENT_SECRET": ID}, "same value in both fields", "identical"),
        ({"GWS_CLIENT_ID": ID}, "secret missing", "Only one of"),
        ({"GWS_CLIENT_SECRET": SECRET}, "id missing", "Only one of"),
    ],
)
def test_bad_credentials_register_nothing(box, creds, why, said):
    proc, calls = _run(box, **creds)
    assert proc.returncode != 0, f"{why}: should have failed loudly"
    assert "mcp add" not in calls, f"{why}: must not register an unusable client"
    # It has to fail at the credential check. A run that dies earlier, on the
    # interpreter or a missing tool, also exits non-zero with nothing registered,
    # and would pass without having tested any of this.
    assert said in proc.stderr, proc.stderr


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

# mktemp, cat and rm are what the version probe uses for its scratch file. They
# are here, and not only in the tests that run the probe, so that a regression
# which probes when it should not still reaches the stand-in it would run,
# instead of failing on a missing tool and leaving the test green.
BASE_TOOLS = ("sed", "cat", "rm", "dirname", "mktemp")


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

    return {"repo": repo, "bin": bindir, "log": log, "root": tmp_path}


def _sealed_env(fx, **extra):
    """PATH is the fixture's bindir and nothing else, so what resolves is decided here."""
    env = {"PATH": str(fx["bin"]), "NO_COLOR": "1", **_throwaway_config(fx["root"])}
    env.update({"GWS_CLIENT_ID": ID, "GWS_CLIENT_SECRET": SECRET})
    env.update(extra)
    return env


def _run_sealed(sealed, **extra):
    return _run_installer(sealed, _sealed_env(sealed, **extra), shim_expected=True)


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


# ---------------------------------------------------------------------------
# Claude Code resolution: PATH, or the desktop app's CLAUDE_CODE_EXECPATH
#
# Running this script from inside the Claude desktop app's own Code tab,
# `claude` is often not on PATH -- the app never adds it -- but the app
# exports CLAUDE_CODE_EXECPATH pointing at the copy of Claude Code it runs
# itself, typically somewhere under "Application Support", spaces included.
# install.sh falls back to that, but only after PATH comes up empty, and only
# once the binary proves it really is Claude Code.
#
# These need the same fully-sealed PATH as the interpreter-discovery tests
# above, for the same reason: this machine has a real `claude` on PATH, and
# prepending a fixture bindir to the *real* PATH (as `box` / `_run` do) can't
# hide it -- only a PATH built from scratch can guarantee `command -v claude`
# finds nothing.
# ---------------------------------------------------------------------------

# perl is what bounds the --version probe to ten seconds, but the probe works
# without it, so it is not one of the tools every test needs. `bare_box` links it
# in when the host has one, a test of the bound asks for it with _require_perl,
# and a test of the no-perl case takes it away. A host with no perl then skips
# only the tests of the bound, not the whole group.


@pytest.fixture
def bare_box(tmp_path):
    """A sealed environment like `sealed`, but with no `claude` anywhere on
    PATH -- not even a real one the host happens to have.
    """
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
    perl = shutil.which("perl")
    if perl is not None:
        (bindir / "perl").symlink_to(perl)
    _exe(bindir / "git", "#!/bin/sh\nexit 0\n")
    # A qualifying interpreter, so the run reaches the claude check at all --
    # interpreter discovery is not what these tests are about.
    _py_shim(bindir / "python3", "3.12")

    log = tmp_path / "calls.log"
    log.touch()

    return {"repo": repo, "bin": bindir, "log": log, "root": tmp_path}


def _run_bare(bare_box, *, stdin="", **extra):
    return _run_installer(bare_box, _sealed_env(bare_box, **extra), shim_expected=False, stdin=stdin)


def _run_bare_interrupted(bare_box, *, signal_name="SIGTERM", ready=lambda: True, **extra):
    """`_run_bare`, but the installer is sent `signal_name` once `ready()` is true."""
    return _run_installer(
        bare_box, _sealed_env(bare_box, **extra), shim_expected=False, interrupt=(signal_name, ready)
    )


def _recording_claude(
    path: Path,
    log: Path,
    version_line: str,
    *,
    version_exit: int = 0,
    version_on: str = "stdout",
    add_exit: int = 0,
) -> None:
    """A shim that answers --version, and otherwise behaves like the `claude`
    shim in `box` / `sealed`: records its argv, and heals on `mcp remove` of
    something not yet registered by exiting 1.

    It records every call, --version included, and does so before it answers: a
    test that needs to know whether the installer asked it anything at all (the
    app's copy must not even be probed when `claude` is on PATH) can only see
    that if the question itself is logged.

    `version_exit` and `version_on` shape how it answers --version (which stream
    it prints on, and what it exits with afterwards); `add_exit` is what
    `mcp add` exits with.
    """
    to_stderr = " >&2" if version_on == "stderr" else ""
    _exe(
        path,
        f"""#!/bin/sh
echo "$*" >> "{log}"
if [ "$1" = "--version" ]; then
  echo "{version_line}"{to_stderr}
  exit {version_exit}
fi
case "$2" in
  remove) exit 1 ;;
  add) exit {add_exit} ;;
esac
exit 0
""",
    )


HANG_SECONDS = 60


def _hanging_claude(path: Path, *, started: Path = None) -> None:
    """A binary that never answers --version (it sleeps for HANG_SECONDS).

    `exec`, so the binary is the sleeper itself. A wrapper that starts a child
    and leaves it holding the output open is a different case, with its own
    stand-in below. With `started`, it first writes that file, so a test can tell
    when the installer is waiting on it.
    """
    sleeper = shutil.which("sleep")
    note = f'echo started > "{started}"\n' if started else ""
    _exe(path, f'#!/bin/sh\n{note}exec "{sleeper}" {HANG_SECONDS}\n')


def _claude_with_a_lingering_child(
    path: Path, log: Path, *, answers: bool, banner: str = "2.1.281 (Claude Code)"
) -> None:
    """A binary that starts a long-lived child when it is asked for its version.

    The child inherits the binary's stdout and stderr and keeps them open for
    HANG_SECONDS, as a wrapper script or a helper process that outlives its
    parent does. With `answers` the binary prints its banner and exits at once:
    it has said what it is, and only its child is slow. Without, it waits for the
    child, so it cannot answer inside the installer's ten seconds. Either way the
    installer must not wait for the child: a shell waits for the process it
    started, not for everything that inherited the output it was given.

    `banner` is what it prints as its answer.
    """
    sleeper = shutil.which("sleep")
    if answers:
        version = f'  echo "{banner}"\n  "{sleeper}" {HANG_SECONDS} &\n  exit 0\n'
    else:
        version = f'  "{sleeper}" {HANG_SECONDS} &\n  wait\n  echo "{banner}"\n  exit 0\n'
    _exe(
        path,
        f"""#!/bin/sh
echo "$*" >> "{log}"
if [ "$1" = "--version" ]; then
{version}fi
case "$2" in
  remove) exit 1 ;;
esac
exit 0
""",
    )


def _probe_leftovers(fx) -> list:
    """Scratch files the version probe left behind in the run's TMPDIR."""
    return sorted(p.name for p in (fx["root"] / "tmp").glob("claude-probe.*"))


def _probe_leftovers_in_the_shared_tmp(marker: str) -> list:
    """Scratch files the version probe left behind in the machine's own /tmp.

    That directory is shared with everything else on the machine, so only a file
    that holds `marker`, which nothing but this run's stand-in prints, is this
    run's.
    """
    left = []
    for p in Path("/tmp").glob("claude-probe.*"):
        try:
            if marker in p.read_text(errors="replace"):
                left.append(p.name)
        except OSError:
            continue
    return sorted(left)


def _assert_the_app_copy_was_refused(proc, execpath) -> None:
    """The app's copy of Claude Code was found and could not be used. That is
    different news from Claude Code being absent, and the message must not tell
    the person it is not installed: it has to name the variable and the path
    that was refused, so they can see what to fix, and then say what to do next.

    What to do next is not to run `claude --version` in Terminal, which for the
    people this is for prints "command not found". And the message promises no
    time: the ten second bound is perl's, and without perl there is none."""
    assert "not installed" not in proc.stderr, proc.stderr
    assert "CLAUDE_CODE_EXECPATH" in proc.stderr, proc.stderr
    assert str(execpath) in proc.stderr, proc.stderr
    assert "claude --version" not in proc.stderr, proc.stderr
    assert "ten seconds" not in proc.stderr, proc.stderr
    assert "put the app's own copy of Claude Code" in proc.stderr, proc.stderr
    assert "run this script again" in proc.stderr, proc.stderr


def _hint_words(stderr: str) -> list:
    """The words a real shell reads from the line the installer prints for
    seeing why `mcp add` failed.

    The line is for pasting, so the only honest check is to give it to a shell
    and see what comes back; reading the quotes by eye would pass a line that
    expands a `$` or runs a backtick. Whatever the quoting gets wrong shows up
    here as a different word.
    """
    lines = stderr.splitlines()
    marker = [i for i, ln in enumerate(lines) if "Run this to see the error" in ln]
    assert marker, f"no hint in the installer's output: {stderr!r}"
    hint = lines[marker[0] + 1].strip()
    proc = subprocess.run(
        [BASH, "-c", 'eval "set -- $1"; printf "%s\\0" "$@"', "bash", hint],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"the hint does not parse as shell words: {hint!r}\n{proc.stderr}"
    return proc.stdout.split("\0")[:-1]


def _require_perl(fx) -> None:
    """The ten-second bound is perl's alarm: a test of it needs perl on the sealed PATH."""
    if not (fx["bin"] / "perl").exists():
        pytest.skip("perl unavailable, so the probe has no ten-second bound to test")


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_execpath_is_used_when_claude_is_not_on_path(bare_box, tmp_path):
    """The reported case: inside the Claude desktop app's Code tab, `claude`
    is not on PATH, but CLAUDE_CODE_EXECPATH points at the app's own copy,
    which lives somewhere with a space in the path (e.g. under "Application
    Support") that is not on PATH either."""
    execdir = (
        tmp_path
        / "Application Support"
        / "Claude"
        / "claude-code"
        / "2.1.281"
        / "claude.app"
        / "Contents"
        / "MacOS"
    )
    execdir.mkdir(parents=True)
    execpath = execdir / "claude"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)")

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # The app's copy is asked what it is before it is used.
    assert calls.splitlines()[0] == "--version", calls
    add = [ln for ln in calls.splitlines() if ln.startswith("mcp add")]
    assert len(add) == 1, f"expected one `mcp add`, got {calls!r}"
    argv = add[0]
    assert "mcp add google-workspace -s user" in argv
    assert f"-e GWS_CLIENT_ID={ID}" in argv
    assert f"-e GWS_CLIENT_SECRET={SECRET}" in argv
    assert " -- " in argv, "without `--` the interpreter path parses as a flag"
    assert argv.rstrip().endswith("server.py")
    # The heal-by-remove step goes through the same binary as the add.
    assert "mcp remove google-workspace -s user" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
@pytest.mark.parametrize("env", [{}, {"CLAUDE_CODE_EXECPATH": ""}], ids=["unset", "empty"])
def test_neither_path_nor_execpath_fails_with_the_not_installed_message(bare_box, env):
    """Negative control: nothing at all resolves to Claude Code. An empty
    CLAUDE_CODE_EXECPATH is no more a refused copy than an unset one."""
    proc, calls = _run_bare(bare_box, **env)

    assert proc.returncode != 0
    assert "mcp add" not in calls
    assert "Claude Code is not installed" in proc.stderr, proc.stderr


@pytest.mark.parametrize("kind", ["missing", "not-executable"])
@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_cannot_be_run_is_named_in_the_message(bare_box, tmp_path, kind):
    """A stale CLAUDE_CODE_EXECPATH, or one that points at a file that is not
    executable, is refused without being run, and the message says which."""
    execpath = tmp_path / "claude-gone"
    if kind == "not-executable":
        execpath.write_text("#!/bin/sh\nexit 0\n")

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode != 0
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, execpath)


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_is_not_claude_code_is_rejected(bare_box, tmp_path):
    """CLAUDE_CODE_EXECPATH is trusted only after it proves itself: an
    arbitrary executable that happens to be sitting at that path, and answers
    --version with something else, must not be used."""
    other = tmp_path / "not-claude"
    _recording_claude(other, bare_box["log"], "some-other-tool 9.9.9")

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(other))

    assert proc.returncode != 0
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, other)


@pytest.mark.parametrize(
    "said, on, code",
    [
        # The shape the stub of a missing tool might print: it names Claude Code
        # and is not Claude Code.
        ("Claude Code is not installed on this machine", "stderr", 127),
        ("Claude Code 2.1.281", "stdout", 0),
        ("2.1.281 (Claude Code) is not installed here", "stdout", 0),
        # More than a version in front of the suffix is not a version.
        ("claude-code 2.1.281 (Claude Code)", "stdout", 0),
        # These start with a digit and end with the suffix, as a banner does, so
        # the rule that a version has no spaces in it is all that keeps them out.
        ("3 warnings from 2.1.281 (Claude Code)", "stdout", 0),
        ("12 | 2.1.281 (Claude Code)", "stdout", 0),
        # The banner is looked for on every line, so a line that is not the banner
        # does not become one because other lines come with it.
        ("see the docs\nClaude Code is not installed on this machine", "stderr", 127),
        ("warning: out of date\nclaude-code 2.1.281 (Claude Code)", "stdout", 0),
    ],
)
@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_only_mentions_claude_code_is_rejected(bare_box, tmp_path, said, on, code):
    """What counts is a line that is exactly what `claude --version` prints,
    "<version> (Claude Code)". An executable that merely mentions Claude Code, in
    an error or in other words, is not it, and must not be registered with
    `mcp add`."""
    execpath = tmp_path / "claude-in-name-only"
    _recording_claude(execpath, bare_box["log"], said, version_exit=code, version_on=on)

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, execpath)


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_says_more_after_its_banner_is_still_used(bare_box, tmp_path):
    """What follows the banner does not matter."""
    execpath = tmp_path / "claude-says-more"
    _exe(
        execpath,
        f"""#!/bin/sh
echo "$*" >> "{bare_box["log"]}"
if [ "$1" = "--version" ]; then
  printf '%s\\n%s\\n' "2.1.281 (Claude Code)" "a newer version is available"
  exit 0
fi
exit 0
""",
    )

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


def _warning(n: int) -> str:
    return f"warning {n}: could not read the settings file, using the defaults"


# Forty lines are about 2.5 KB: a lot more than any real Claude Code prints ahead
# of its banner, and still inside the part of the answer the installer reads.
A_PAGE_OF_WARNINGS = "\n".join(_warning(n) for n in range(40))


@pytest.mark.parametrize(
    "first, on",
    [
        (_warning(0), "stderr"),
        (_warning(0), "stdout"),
        ("", "stdout"),
        (A_PAGE_OF_WARNINGS, "stderr"),
    ],
    ids=["a-warning-on-stderr", "a-warning-on-stdout", "a-blank-line", "a-page-of-warnings-on-stderr"],
)
@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_prints_something_before_its_banner_is_still_used(bare_box, tmp_path, first, on):
    """The banner does not have to be the first line. The installer reads both
    streams as one answer, so a warning printed ahead of the banner, on either
    stream, a page of them, or a blank line, must not turn a real Claude Code
    away."""
    to_stderr = " >&2" if on == "stderr" else ""
    execpath = tmp_path / "claude-warns-first"
    _exe(
        execpath,
        f"""#!/bin/sh
echo "$*" >> "{bare_box["log"]}"
if [ "$1" = "--version" ]; then
  echo "{first}"{to_stderr}
  echo "2.1.281 (Claude Code)"
  exit 0
fi
case "$2" in
  remove) exit 1 ;;
esac
exit 0
""",
    )

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_buries_its_banner_under_pages_of_output_is_refused_promptly(bare_box, tmp_path):
    """Only the start of the answer is looked at. A real Claude Code prints one
    line, and bash takes far longer than anything the installer is worth waiting
    for to go through thousands of lines one at a time: the time grows much faster
    than the length. A banner after that much output is not looked for, so the
    copy is refused, and quickly.

    The elapsed time is what tells an installer that reads it all from one that
    does not, so the bound is the point of the test and the refusal only follows
    from it."""
    noise = tmp_path / "pages-of-output.txt"
    noise.write_text("".join(_warning(n) + "\n" for n in range(6000)))
    execpath = tmp_path / "claude-buries-its-banner"
    _exe(
        execpath,
        f"""#!/bin/sh
echo "$*" >> "{bare_box["log"]}"
if [ "$1" = "--version" ]; then
  cat "{noise}"
  echo "2.1.281 (Claude Code)"
  exit 0
fi
exit 0
""",
    )

    started = time.monotonic()
    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))
    elapsed = time.monotonic() - started

    assert elapsed < 20, f"the installer went through the whole answer: it took {elapsed:.0f}s"
    assert proc.returncode != 0
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, execpath)


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_prints_its_banner_and_then_exits_non_zero_is_still_used(bare_box, tmp_path):
    """What a binary says it is and how it exits are separate questions. The
    installer runs under `pipefail`, where a non-zero exit from the thing being
    asked would fail a whole `... | grep` pipeline and throw out a binary that
    had just said "Claude Code"."""
    execpath = tmp_path / "claude-exits-7"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)", version_exit=7)

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_says_what_it_is_on_stderr_is_still_used(bare_box, tmp_path):
    """Some CLIs print their version banner on stderr. Reading only stdout would
    reject a real Claude Code for it."""
    execpath = tmp_path / "claude-says-it-on-stderr"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)", version_on="stderr")

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_that_hangs_on_version_is_given_up_on(bare_box, tmp_path):
    """A binary that never answers --version must not hang the installer. It is
    cut off after ten seconds and counts as not being Claude Code.

    The binary here hangs for HANG_SECONDS, long enough that an installer with
    no bound finishes much later than one with it; the elapsed-time check is
    what tells the two apart, because a hang that eventually ends and prints
    nothing is rejected either way."""
    execpath = tmp_path / "claude-hangs"
    _hanging_claude(execpath)
    _require_perl(bare_box)

    started = time.monotonic()
    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))
    elapsed = time.monotonic() - started

    assert elapsed < HANG_SECONDS - 20, (
        f"the --version probe was not bounded: the installer took {elapsed:.0f}s "
        f"against a binary that hangs for {HANG_SECONDS}s"
    )
    assert proc.returncode != 0
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, execpath)


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_child_that_keeps_the_output_open_does_not_stretch_the_wait(bare_box, tmp_path):
    """The binary answered and exited; a helper it started still holds the output
    it was given. The installer waits for the binary it ran, not for everything
    that inherited the output, so the answer is used at once."""
    execpath = tmp_path / "claude-leaves-a-child"
    _claude_with_a_lingering_child(execpath, bare_box["log"], answers=True)

    started = time.monotonic()
    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))
    elapsed = time.monotonic() - started

    assert elapsed < HANG_SECONDS - 20, (
        f"the installer waited for a child of the binary it asked: it took {elapsed:.0f}s, "
        f"and the child lives for {HANG_SECONDS}s"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls
    assert _probe_leftovers(bare_box) == []


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_tmpdir_that_does_not_exist_does_not_bring_back_the_wait_for_a_child(bare_box, tmp_path):
    """A TMPDIR that names a directory that does not exist is not "no mktemp": the
    scratch file is made in /tmp instead, so the answer still goes through a file
    and a child that keeps the output open still cannot stretch the wait. Read
    through $(...) instead, the installer would wait for that child to finish.

    /tmp is the machine's own, shared directory, so the stand-in prints a banner
    no one else prints, and only a scratch file that holds it counts as left
    behind by this run."""
    if not os.access("/tmp", os.W_OK):
        pytest.skip("/tmp is not writable here, so there is no fallback scratch file to test")
    banner = f"2.1.{os.getpid()}.{time.time_ns()} (Claude Code)"
    execpath = tmp_path / "claude-leaves-a-child"
    _claude_with_a_lingering_child(execpath, bare_box["log"], answers=True, banner=banner)
    gone = tmp_path / "no-such-dir"
    # Control: mktemp really cannot use this TMPDIR, so the retry is what is tested.
    assert not gone.exists()

    started = time.monotonic()
    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath), TMPDIR=str(gone))
    elapsed = time.monotonic() - started

    assert elapsed < HANG_SECONDS - 20, (
        f"the installer waited for a child of the binary it asked: it took {elapsed:.0f}s, "
        f"and the child lives for {HANG_SECONDS}s"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls
    assert not gone.exists()
    assert _probe_leftovers(bare_box) == []
    assert _probe_leftovers_in_the_shared_tmp(banner.split(" ")[0]) == []


def test_the_shared_tmp_check_sees_a_scratch_file_that_holds_its_marker():
    """Negative control for the check above, which would pass for any file name or
    place it was not looking at: a scratch file shaped like the probe's, holding
    the marker, has to be found, and one holding something else must not be."""
    if not os.access("/tmp", os.W_OK):
        pytest.skip("/tmp is not writable here")
    marker = f"2.1.{os.getpid()}.{time.time_ns()}"
    planted = []
    try:
        for text in (f"{marker} (Claude Code)\n", "something else\n"):
            fd, name = tempfile.mkstemp(prefix="claude-probe.", dir="/tmp")
            planted.append(Path(name))
            with os.fdopen(fd, "w") as handle:
                handle.write(text)

        assert _probe_leftovers_in_the_shared_tmp(marker) == [planted[0].name]
    finally:
        for p in planted:
            p.unlink(missing_ok=True)
    assert _probe_leftovers_in_the_shared_tmp(marker) == []


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_wrapper_whose_child_outlives_the_bound_is_still_cut_off_on_time(bare_box, tmp_path):
    """The ten second bound has to hold for a binary that is a wrapper around a
    child it waits for. The alarm ends the wrapper; whatever the child still has
    open must not stretch the wait past it."""
    execpath = tmp_path / "claude-wrapper"
    _claude_with_a_lingering_child(execpath, bare_box["log"], answers=False)
    _require_perl(bare_box)

    started = time.monotonic()
    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))
    elapsed = time.monotonic() - started

    assert elapsed < HANG_SECONDS - 20, (
        f"the --version probe was not bounded: the installer took {elapsed:.0f}s "
        f"against a wrapper whose child lives for {HANG_SECONDS}s"
    )
    assert proc.returncode != 0
    assert "mcp add" not in calls
    _assert_the_app_copy_was_refused(proc, execpath)
    assert _probe_leftovers(bare_box) == []


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGHUP", "SIGTERM"])
@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_signal_while_the_probe_waits_leaves_no_scratch_file(bare_box, tmp_path, signal_name):
    """Someone presses Ctrl-C, or closes the window, while the probe waits on a
    binary that is not answering (ten seconds with perl, with no limit without
    it). The installer ends as it always did, with the status a shell reports for
    that signal, and the scratch file the probe was writing the answer to is not
    left behind in TMPDIR.

    The signal goes to the installer's whole process group, as a terminal sends
    it, and only once the stand-in for the app's copy has started: the probe is
    then certainly waiting on it, and has long since made its file."""
    started = tmp_path / "claude-has-started.log"
    execpath = tmp_path / "claude-hangs-when-asked"
    _hanging_claude(execpath, started=started)

    proc, calls = _run_bare_interrupted(
        bare_box, signal_name=signal_name, ready=started.exists, CLAUDE_CODE_EXECPATH=str(execpath)
    )

    assert _status_a_shell_reports(proc.returncode) == 128 + getattr(signal, signal_name), (
        proc.returncode, proc.stdout, proc.stderr
    )
    assert "mcp add" not in calls
    assert _probe_leftovers(bare_box) == []


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_file_that_takes_the_probes_scratch_name_later_is_left_alone(bare_box, tmp_path):
    """The EXIT trap removes whatever PROBE_OUT names, so once the probe has removed
    its own file it has to forget the name: after that the name is not the
    installer's, and a file somebody else puts under it survives however the
    installer ends. A `mktemp` that always hands out the same name makes that reuse
    certain; with the real one it would take a random name coming up twice."""
    taken = bare_box["root"] / "tmp" / "claude-probe.REUSED"
    (bare_box["bin"] / "mktemp").unlink()
    _exe(bare_box["bin"] / "mktemp", f'#!/bin/sh\n: > "{taken}"\nprintf "%s\\n" "{taken}"\n')
    execpath = tmp_path / "claude-app"
    _exe(
        execpath,
        f"""#!/bin/sh
echo "$*" >> "{bare_box["log"]}"
if [ "$1" = "--version" ]; then echo "2.1.281 (Claude Code)"; exit 0; fi
case "$2" in
  remove) exit 1 ;;
  add) echo "somebody else's file" > "{taken}"; exit 0 ;;
esac
exit 0
""",
    )

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls
    assert taken.read_text() == "somebody else's file\n"


@pytest.mark.parametrize("with_perl", [True, False], ids=["bounded-by-perl", "unbounded-without-perl"])
@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_the_version_probe_never_reads_the_installers_stdin(bare_box, tmp_path, with_perl):
    """The installer's stdin can be a terminal, or the script itself when it is
    piped in. The binary asked for its version must not be handed it: one that
    reads its stdin would swallow whatever is waiting there. Both ways of asking
    are pinned, the one bounded by perl and the one that is not."""
    if with_perl:
        _require_perl(bare_box)
    else:
        (bare_box["bin"] / "perl").unlink(missing_ok=True)
    seen = tmp_path / "stdin-seen-by-claude.log"
    execpath = tmp_path / "claude-reads-stdin"
    _exe(
        execpath,
        f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  cat > "{seen}"
  echo "2.1.281 (Claude Code)"
  exit 0
fi
exit 0
""",
    )

    proc, _ = _run_bare(bare_box, stdin="typed by the person\n", CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert seen.exists(), "the stand-in was never asked for its version"
    assert seen.read_text() == "", "the version probe was handed the installer's stdin"


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_is_still_used_where_perl_is_missing(bare_box, tmp_path):
    """The ten-second bound needs perl. Without it the question is asked
    unbounded rather than not asked: no perl must never mean no fallback."""
    (bare_box["bin"] / "perl").unlink(missing_ok=True)
    execpath = tmp_path / "claude"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)")
    # Control: this PATH really has no perl, so the unbounded branch is the one that runs.
    assert _command_found_on(_sealed_env(bare_box), "perl") == ""

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_an_execpath_is_still_used_where_mktemp_is_missing(bare_box, tmp_path):
    """The probe writes its answer to a scratch file, but a missing or unusable
    mktemp must not mean a refused Claude Code: it falls back to reading the
    answer through $(...), slower to give up on a child but still asked."""
    (bare_box["bin"] / "mktemp").unlink()
    execpath = tmp_path / "claude"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)")
    # Control: this PATH really has no mktemp, so the fallback is the branch that runs.
    assert _command_found_on(_sealed_env(bare_box), "mktemp") == ""

    proc, calls = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_failed_registration_names_the_binary_that_was_run(bare_box, tmp_path):
    """The hint for seeing the error has to be a command that works for the
    person reading it. The desktop app's copy of Claude Code is not on PATH, so
    a bare `claude` would not be; its path has a space in it, so it is quoted."""
    execdir = tmp_path / "Application Support" / "Claude"
    execdir.mkdir(parents=True)
    execpath = execdir / "claude"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)", add_exit=1)

    proc, _ = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode != 0
    assert "Could not register the connector" in proc.stderr, proc.stderr
    assert f"'{execpath}' mcp add google-workspace" in proc.stderr, proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_the_failed_registration_hint_survives_an_odd_path(bare_box, tmp_path):
    """The hint is for pasting into a shell, and a path in it can hold anything a
    filename can: spaces, quotes, a `$`, a backtick. Quoted wrongly, the pasted
    line expands or runs something and starts a different command from the one
    that failed. Every word read back has to be exactly what ran, the binary and
    the interpreter and server paths alike."""
    odd = "odd 'single' \"double\" $HOME `echo hi` ;&(x)"
    execdir = tmp_path / odd / "Application Support"
    execdir.mkdir(parents=True)
    execpath = execdir / "claude"
    _recording_claude(execpath, bare_box["log"], "2.1.281 (Claude Code)", add_exit=1)
    # The clone lives under an odd path too, so $VENV_PY and $SCRIPT_DIR are covered.
    repo = tmp_path / odd / "repo"
    shutil.copytree(bare_box["repo"], repo, symlinks=True)
    bare_box["repo"] = repo

    proc, _ = _run_bare(bare_box, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode != 0
    assert _hint_words(proc.stderr) == [
        str(execpath), "mcp", "add", "google-workspace", "-s", "user",
        "-e", "GWS_CLIENT_ID=...", "-e", "GWS_CLIENT_SECRET=...", "--",
        str(repo / ".venv" / "bin" / "python"), str(repo / "server.py"),
    ]


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_a_failed_registration_through_claude_on_path_still_says_claude(sealed):
    """Nothing changes for the common case: with `claude` on PATH the hint is
    the plain `claude mcp add ...` it always was."""
    _py_shim(sealed["bin"] / "python3", "3.12")
    _exe(sealed["bin"] / "claude", '#!/bin/sh\ncase "$2" in add) exit 1 ;; esac\nexit 0\n')

    proc, _ = _run_sealed(sealed)

    assert proc.returncode != 0
    assert "     claude mcp add google-workspace" in proc.stderr, proc.stderr
    assert _hint_words(proc.stderr) == [
        "claude", "mcp", "add", "google-workspace", "-s", "user",
        "-e", "GWS_CLIENT_ID=...", "-e", "GWS_CLIENT_SECRET=...", "--",
        str(sealed["repo"] / ".venv" / "bin" / "python"), str(sealed["repo"] / "server.py"),
    ]


@pytest.mark.skipif(BASH is None, reason="bash not found")
def test_path_wins_over_execpath(sealed, tmp_path):
    """`claude` on PATH is still the first answer; CLAUDE_CODE_EXECPATH is
    only a fallback for when PATH has nothing."""
    _py_shim(sealed["bin"] / "python3", "3.12")

    execpath_log = tmp_path / "execpath-calls.log"
    execpath_log.touch()
    execpath_dir = tmp_path / "unused-execpath"
    execpath_dir.mkdir()
    execpath = execpath_dir / "claude"
    _recording_claude(execpath, execpath_log, "2.1.281 (Claude Code)")

    proc, calls = _run_sealed(sealed, CLAUDE_CODE_EXECPATH=str(execpath))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mcp add google-workspace" in calls
    # Not even asked what it is: every call the shim gets is logged, --version too.
    assert execpath_log.read_text() == "", "the execpath shim must not have been invoked"


# ---------------------------------------------------------------------------
# The harness itself
#
# The guard that keeps the installer away from a real Claude Code is only worth
# having if it can fail. These show that every PATH the fixtures build passes it,
# that it fails on the two things it exists to catch, and that the `claude` the
# installer launches really does receive the throwaway config.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name, env_of, shim_expected",
    [
        ("box", _box_env, True),
        ("sealed", _sealed_env, True),
        ("bare_box", _sealed_env, False),
    ],
)
def test_no_fixture_can_reach_a_real_claude(request, fixture_name, env_of, shim_expected):
    """Every PATH a fixture builds resolves `claude` to its own shim, or to
    nothing: never to whatever the host happens to have installed."""
    fx = request.getfixturevalue(fixture_name)

    _assert_nothing_real_is_reachable(env_of(fx), fx["root"], shim_expected=shim_expected)


@pytest.mark.parametrize(
    "fixture_name, runner",
    [
        ("box", _run),
        ("sealed", _run_sealed),
        ("bare_box", _run_bare),
        ("bare_box", _run_bare_interrupted),
    ],
)
def test_every_runner_checks_the_guard_before_it_starts_the_installer(
    request, monkeypatch, fixture_name, runner
):
    """The guard protects nothing if a run can skip it. With the guard swapped
    for one that raises, each runner has to raise: take the guard call out of
    the shared runner, or let one of these start install.sh itself, and the
    installer runs instead and this fails."""

    class GuardReached(Exception):
        pass

    def tripwire(env, root, *, shim_expected):
        raise GuardReached

    monkeypatch.setattr(sys.modules[__name__], "_assert_nothing_real_is_reachable", tripwire)
    fx = request.getfixturevalue(fixture_name)

    # Credentials are passed so that a run which does get through never stops to
    # ask for them.
    with pytest.raises(GuardReached):
        runner(fx, GWS_CLIENT_ID=ID, GWS_CLIENT_SECRET=SECRET)


def test_the_guard_fails_when_a_real_claude_is_reachable(tmp_path):
    """Negative control. On a machine with no `claude` at all (CI) every test
    above passes with the guard deleted, so the guard has to be shown failing."""
    root = tmp_path / "fixture"
    root.mkdir()
    host = tmp_path / "host-install"  # stands in for wherever a real claude lives
    host.mkdir()
    _exe(host / "claude", "#!/bin/sh\nexit 0\n")
    env = {"PATH": str(host), "NO_COLOR": "1", **_throwaway_config(root)}

    with pytest.raises(AssertionError, match="real claude is reachable"):
        _assert_nothing_real_is_reachable(env, root, shim_expected=True)
    with pytest.raises(AssertionError, match="expected no claude"):
        _assert_nothing_real_is_reachable(env, root, shim_expected=False)


@pytest.mark.parametrize("key", ["HOME", "CLAUDE_CONFIG_DIR"])
def test_the_guard_fails_when_the_config_would_not_be_a_throwaway(tmp_path, key):
    """Negative control for the other layer: a HOME or CLAUDE_CONFIG_DIR that
    points outside the test's own tmp dir is refused."""
    root = tmp_path / "fixture"
    root.mkdir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    env = {"PATH": str(root / "empty"), "NO_COLOR": "1", **_throwaway_config(root), key: str(elsewhere)}

    with pytest.raises(AssertionError, match=key):
        _assert_nothing_real_is_reachable(env, root, shim_expected=False)


def test_box_pins_python3_so_the_host_cannot_decide_the_outcome(box):
    """`box` keeps the machine's real PATH behind its own dir. install.sh needs a
    python3 of 3.10 or newer from that PATH, so without a pin the interpreter
    these tests run against is whichever python3 the host has first: on a stock
    Mac the system 3.9, which fails the tests that must succeed and passes the
    ones that must fail without ever reaching the check they are about."""
    found = _command_found_on(_box_env(box), "python3")

    assert found, "no python3 on the box PATH at all"
    assert Path(found).resolve().is_relative_to(box["root"].resolve()), (
        f"the box PATH finds the host's python3 ({found}), so what these tests "
        f"prove depends on which Python the machine running them has"
    )


def test_the_guard_fails_when_claude_code_execpath_points_outside_the_tmp_dir(tmp_path):
    """Negative control for the installer's second way of reaching a binary.
    CLAUDE_CODE_EXECPATH names something install.sh runs with --version, and as
    `claude mcp add` if it answers as Claude Code, so when it is set it has to
    resolve inside the test's own tmp dir, symlinks followed."""
    root = tmp_path / "fixture"
    root.mkdir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    real = elsewhere / "claude"
    _exe(real, "#!/bin/sh\nexit 0\n")
    inside = root / "claude"
    _exe(inside, "#!/bin/sh\nexit 0\n")
    disguised = root / "claude-link"
    disguised.symlink_to(real)
    env = {"PATH": str(root / "empty"), "NO_COLOR": "1", **_throwaway_config(root)}

    for outside in (real, disguised):
        with pytest.raises(AssertionError, match="CLAUDE_CODE_EXECPATH"):
            _assert_nothing_real_is_reachable(
                {**env, "CLAUDE_CODE_EXECPATH": str(outside)}, root, shim_expected=False
            )
    _assert_nothing_real_is_reachable({**env, "CLAUDE_CODE_EXECPATH": str(inside)}, root, shim_expected=False)
    # An empty value is "not set" to install.sh, so it is to the guard.
    _assert_nothing_real_is_reachable({**env, "CLAUDE_CODE_EXECPATH": ""}, root, shim_expected=False)


def test_the_claude_the_installer_runs_only_sees_a_throwaway_config(box):
    """What matters is not what the harness hands the installer but what the
    `claude` the installer launches actually receives."""
    seen = box["root"] / "seen-by-claude.log"
    _exe(
        box["bin"] / "claude",
        f'#!/bin/sh\necho "$HOME|$CLAUDE_CONFIG_DIR" >> "{seen}"\nexit 0\n',
    )

    proc, _ = _run(box, GWS_CLIENT_ID=ID, GWS_CLIENT_SECRET=SECRET)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    runs = [ln.split("|") for ln in seen.read_text().splitlines()]
    assert len(runs) == 2, f"expected the remove and the add, got {runs!r}"
    root = box["root"].resolve()
    for home, config in runs:
        assert Path(home).resolve().is_relative_to(root), home
        assert Path(config).resolve().is_relative_to(root), config
