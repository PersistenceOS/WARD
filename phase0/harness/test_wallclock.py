"""Soak test for run_capped (Phase-2 week 0, R5): proves the wall-clock cap is
a hard bound that kills the WHOLE process tree — even when a grandchild
inherits the stdout pipe (the exact 14,418 s hang scenario from 2026-08-01).

Runs without any external model or dafny: pure subprocess behavior.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from harness.models import OpenCodeModel
from harness.wallclock import WallClockTimeoutError, run_capped

# Parent spawns a grandchild that INHERITS stdout (keeps the pipe open), writes
# the grandchild's pid to a marker file, then sleeps forever. This reproduces
# the failure mode where killing only the direct child leaves the pipe open and
# the drain blocks forever.
HANG_SCRIPT = (
    "import subprocess, sys, time\n"
    "marker = sys.argv[1]\n"
    "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "open(marker, 'w').write(str(gc.pid))\n"
    "time.sleep(600)\n"
)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


class TestRunCapped(unittest.TestCase):
    def test_quick_command_returns_output(self):
        proc = run_capped(
            [sys.executable, "-c", "print('hello')"],
            timeout=30,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("hello", proc.stdout)

    def test_simple_hang_raises_within_cap(self):
        t0 = time.monotonic()
        with self.assertRaises(WallClockTimeoutError):
            run_capped(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                timeout=2,
                capture_output=True,
                text=True,
            )
        elapsed = time.monotonic() - t0
        # cap 2 s + grace 10 s -> must return well under 20 s
        self.assertLess(elapsed, 20, f"hang not bounded: {elapsed:.1f}s")

    def test_grandchild_tree_killed(self):
        """The exact hang scenario: a grandchild holds the pipe. run_capped
        must return within cap+grace and leave no survivor in the tree."""
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "pid.txt"
            t0 = time.monotonic()
            with self.assertRaises(WallClockTimeoutError):
                run_capped(
                    [sys.executable, "-c", HANG_SCRIPT, str(marker)],
                    timeout=2,
                    capture_output=True,
                    text=True,
                )
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 20, f"hang not bounded: {elapsed:.1f}s")
            self.assertTrue(marker.exists(), "marker never written — script didn't run?")
            pid = int(marker.read_text().strip())
            # give the OS a moment to reap, then assert the grandchild is gone
            time.sleep(1.5)
            self.assertFalse(
                _process_exists(pid), f"grandchild {pid} survived the tree kill"
            )

    def test_no_capture_leaves_stdout_none(self):
        # Without capture_output, stdout/stderr are inherited (None on the
        # CompletedProcess) — the no-capture path must not crash.
        proc = run_capped([sys.executable, "-c", "print('x')"], timeout=30)
        self.assertIsNone(proc.stdout)
        self.assertEqual(proc.returncode, 0)

    def test_stdin_pipe_forwarded(self):
        proc = run_capped(
            [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
            timeout=30,
            capture_output=True,
            text=True,
            stdin=subprocess.PIPE,
        )
        self.assertEqual(proc.returncode, 0)

    def test_opencode_generate_end_to_end(self):
        """Regression (2026-08-01 review): OpenCodeModel.generate must pass
        capture_output=True to run_capped, or proc.stdout is None and the parse
        loop crashes with AttributeError on every real-model call. A fake
        opencode CLI (a .bat/.sh shim that prints opencode-style JSON events)
        exercises generate -> run_capped -> parse end to end."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            fake_py = td / "fake_opencode.py"
            fake_py.write_text(
                "import sys, json\n"
                "print(json.dumps({'type': 'message.part.updated', 'part': {'type': 'text', 'text': 'fn f() -> int { return 1; }'}}))\n"
                "print(json.dumps({'type': 'other', 'part': {}}))\n",
                encoding="utf-8",
            )
            if os.name == "nt":
                cli = td / "fake_opencode.bat"
                cli.write_text(
                    f'@echo off\r\n"{sys.executable}" "{fake_py}" %*\r\n',
                    encoding="utf-8",
                )
            else:
                cli = td / "fake_opencode.sh"
                cli.write_text(
                    f'#!/bin/sh\n"{sys.executable}" "{fake_py}" "$@"\n',
                    encoding="utf-8",
                )
                cli.chmod(0o755)
            model = OpenCodeModel(model="fake-model", guide="guide")
            model.opencode = str(cli)
            out = model.generate("spec", "t1_abs", 1)
            self.assertEqual(out, "fn f() -> int { return 1; }")

    def test_grace_does_not_allow_unbounded_drain(self):
        # A process that refuses to die even after the tree kill (hard to
        # arrange portably) is still bounded by the grace cap on the drain.
        # Here we just assert a hang with a tiny grace returns in bounded time.
        t0 = time.monotonic()
        with self.assertRaises(WallClockTimeoutError):
            run_capped(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                timeout=1,
                grace=3,
                capture_output=True,
                text=True,
            )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 15, f"grace not bounded: {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
