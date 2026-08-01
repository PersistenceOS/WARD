"""Wall-clock-bounded subprocess execution (Phase-2 week 0, requirement R5).

Why this exists: ``subprocess.run(..., timeout=...)`` kills only the *direct*
child process. When that child spawns a grandchild that inherits the stdout /
stderr pipe (e.g. ``opencode`` spawning a worker process), the post-timeout
drain inside ``subprocess.run`` blocks forever on the still-open pipe —
observed 2026-08-01 as a 14,418 s hang on a single attempt despite the 180 s
model timeout. The model's own timeout is therefore not a bound on wall clock.

The fix: spawn every long-running call in its own process group / session and
enforce the cap with a subprocess-level *tree* kill — ``taskkill /F /T`` on
Windows, ``killpg(SIGKILL)`` on POSIX — so the whole tree dies, the pipe
closes, and the drain completes. The drain itself has a small secondary
``grace`` cap so this module can never hang.

Contract (from the Phase-2 scoping doc, R5): every model call and every dafny
verify call has a hard wall-clock cap enforced *outside* the child's own
timing, and a soak test proves the bound before any real-model run.
"""

import os
import signal
import subprocess
import time

__all__ = ["WallClockTimeoutError", "run_capped"]


class WallClockTimeoutError(TimeoutError):
    """Raised when a command exceeds its wall-clock cap (after tree-kill)."""


def _tree_kill(proc: subprocess.Popen) -> None:
    """Kill the process AND all descendants, not just the direct child."""
    if os.name == "nt":
        # taskkill /F /T terminates the process and every descendant. Guarded:
        # if taskkill itself ever raised, the tree kill must not be skipped.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    # Fallback: kill the direct child too (no-op if already dead).
    try:
        proc.kill()
    except OSError:
        pass


def run_capped(
    cmd: list[str],
    timeout: float,
    grace: float = 10.0,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` with a hard wall-clock cap enforced by a process-tree kill.

    Mirrors ``subprocess.run(cmd, capture_output=..., timeout=...)`` except the
    timeout is enforced outside the child's own timing and terminates the
    whole process tree. On timeout, raises :class:`WallClockTimeoutError`
    *after* the tree is dead.

    Accepted kwargs (subset of :class:`subprocess.Popen`): ``stdin``,
    ``stdout``, ``stderr``, ``capture_output``, ``text``, ``encoding``,
    ``errors``, ``env``, ``cwd``, ``shell`` — everything else is forwarded to
    :class:`subprocess.Popen` unchanged.
    """
    stdin = kwargs.pop("stdin", None)
    stdout = kwargs.pop("stdout", None)
    stderr = kwargs.pop("stderr", None)
    capture = kwargs.pop("capture_output", False)
    text = kwargs.pop("text", False)
    encoding = kwargs.pop("encoding", None)
    errors = kwargs.pop("errors", None)
    if capture:
        if stdout is not None or stderr is not None:
            raise ValueError("stdout/stderr may not be used with capture_output")
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE

    popen_kwargs = dict(kwargs)
    if os.name == "nt":
        popen_kwargs["creationflags"] = popen_kwargs.get("creationflags", 0) | (
            subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        cmd,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=text,
        encoding=encoding,
        errors=errors,
        **popen_kwargs,
    )
    start = time.monotonic()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _tree_kill(proc)
        # Drain with a small secondary cap: after a successful tree-kill the
        # pipes close and this returns immediately; the cap exists so a stray
        # grandchild beyond the tree (parent already dead) can never hang us.
        try:
            out, err = proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            out, err = None, None
        elapsed = time.monotonic() - start
        raise WallClockTimeoutError(
            f"command exceeded wall-clock cap of {timeout}s "
            f"(elapsed {elapsed:.1f}s); process tree killed"
        ) from None
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
