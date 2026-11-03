"""DAT-1236 D2: scripts/demo.sh must stop its server's process group even when ps cannot run.

/bin/ps is setuid root, and sandbox-exec refuses to run it ("execvp() of 'ps' failed: Operation not
permitted"), so in `make clean-clone-test` pass 2 the old group_alive was always false, cleanup never
signalled the group, and the server kept listening after PASS. These tests run demo.sh's own
group_alive and cleanup functions, cut from the script, with `ps` replaced on PATH by a stub that fails
the same way. They never depend on an OS limit, and they stop only the process group they start.
"""
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "scripts" / "demo.sh"


def _function(src: str, name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", src, re.M | re.S)
    assert m, f"{name}() not found in scripts/demo.sh"
    return m.group(0)


def _failing_ps(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "ps"
    stub.write_text("#!/bin/sh\necho \"sandbox-exec: execvp() of 'ps' failed: Operation not permitted\" >&2\nexit 71\n")
    stub.chmod(0o755)
    return bin_dir


def _group(ignore_term: bool) -> subprocess.Popen:
    """A stand-in server: a group leader with two children, in a process group of its own."""
    body = ("trap '' TERM; " if ignore_term else "") + "/bin/sleep 300 & /bin/sleep 300 & wait"
    return subprocess.Popen(["/bin/bash", "-c", body], process_group=0,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _group_exists(pgid: int) -> bool:
    """True while this test can still signal a process in the group. EPERM counts as gone: once the leader is
    reaped, children reparented to launchd can linger as zombies we may no longer signal (seen under load, where
    killpg(pgid, 0) raised EPERM, not ESRCH), and a process of ours that is alive is always ours to signal."""
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_gone(pgid: int, seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while _group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.1)


def _harness(tmp_path: Path, body: str) -> Path:
    src = DEMO.read_text()
    script = tmp_path / "harness.sh"
    script.write_text("set -u\nPGID=\"$1\" SERVER_PID=\"\" WARM_PID=\"\"\n"
                      + _function(src, "group_alive") + _function(src, "cleanup") + body)
    return script


def _run(tmp_path: Path, script: Path, pgid: int) -> subprocess.CompletedProcess:
    env = {"PATH": f"{_failing_ps(tmp_path)}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "en_US.UTF-8"}
    return subprocess.run(["/bin/bash", str(script), str(pgid)], env=env, capture_output=True, text=True, timeout=60)


# The harness checks that the stub is the ps it gets and that group_alive still sees the live group,
# then leaves through cleanup exactly as demo.sh does (trap on EXIT).
CHECKS = """
if ps -ax -o pgid=,stat= >/dev/null 2>&1; then echo "ps did not fail: the stub is not in use"; exit 97; fi
group_alive || { echo "group_alive is false while the group is alive"; exit 98; }
trap cleanup EXIT
exit 0
"""


@pytest.mark.parametrize("ignore_term", [False, True], ids=["stops-on-TERM", "ignores-TERM-so-KILL"])
def test_cleanup_stops_the_group_when_ps_cannot_run(tmp_path, ignore_term):
    proc = _group(ignore_term)
    pgid = proc.pid
    try:
        time.sleep(0.3)  # let the leader start its children
        assert _group_exists(pgid)
        r = _run(tmp_path, _harness(tmp_path, CHECKS), pgid)
        assert r.returncode == 0, r.stdout + r.stderr
        proc.wait(timeout=10)  # reap the leader; its children got the same group signal
        _wait_gone(pgid)
        assert not _group_exists(pgid), "the server's process group outlived cleanup"
        if ignore_term:
            assert "did not stop within 5 s; killing it" in r.stderr
    finally:
        if _group_exists(pgid):
            os.killpg(pgid, signal.SIGKILL)  # only the group this test started
        proc.wait(timeout=10)


def _server_group(tmp_path: Path, pid: int) -> subprocess.CompletedProcess:
    """demo.sh's server_group, cut from the script, run with SERVER_PID set and ps failing, under set -euo pipefail
    as in demo.sh, so a failing ps that ended the script would show here as a non-zero exit."""
    script = tmp_path / "server_group.sh"
    script.write_text("set -euo pipefail\nSERVER_PID=\"$1\"\n" + _function(DEMO.read_text(), "server_group")
                      + 'g="$(server_group)"\nprintf "group=%s\\n" "$g"\n')
    env = {"PATH": f"{_failing_ps(tmp_path)}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "en_US.UTF-8"}
    return subprocess.run(["/bin/bash", str(script), str(pid)], env=env, capture_output=True, text=True, timeout=30)


def test_server_group_without_ps_names_its_own_group(tmp_path):
    proc = _group(ignore_term=False)
    try:
        r = _server_group(tmp_path, proc.pid)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == f"group={proc.pid}"
    finally:
        os.killpg(proc.pid, signal.SIGKILL)  # only the group this test started
        proc.wait(timeout=10)


def test_server_group_without_ps_flags_a_server_in_another_group(tmp_path):
    proc = subprocess.Popen(["/bin/sleep", "300"], stdin=subprocess.DEVNULL)  # stays in pytest's own group
    try:
        r = _server_group(tmp_path, proc.pid)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "group=unknown"
    finally:
        proc.kill()  # by PID: only the process this test started
        proc.wait(timeout=10)


def test_server_group_without_ps_is_empty_once_the_server_is_gone(tmp_path):
    proc = _group(ignore_term=False)
    os.killpg(proc.pid, signal.SIGKILL)  # only the group this test started
    proc.wait(timeout=10)
    _wait_gone(proc.pid)
    r = _server_group(tmp_path, proc.pid)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "group="


def test_group_alive_is_false_once_the_group_is_gone(tmp_path):
    proc = _group(ignore_term=False)
    pgid = proc.pid
    time.sleep(0.3)
    os.killpg(pgid, signal.SIGKILL)  # only the group this test started
    proc.wait(timeout=10)
    _wait_gone(pgid)
    r = _run(tmp_path, _harness(tmp_path, "group_alive && exit 1; exit 0\n"), pgid)
    assert r.returncode == 0, "group_alive said a dead group was alive: " + r.stdout + r.stderr
