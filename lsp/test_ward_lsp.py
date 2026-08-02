"""Tests for ward_lsp.py — unit (line mapping, URIs) + integration.

The integration test spawns the real server over stdio and drives the LSP
protocol: initialize -> didOpen(buggy) -> diagnostics with a counterexample;
didSave(fixed) -> empty diagnostics; shutdown/exit -> clean exit.

Run from the repo root:
    phase0/.venv/Scripts/python -m lsp.test_ward_lsp
or:
    python lsp/test_ward_lsp.py
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "ward_lsp.py"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import ward_lsp  # noqa: E402  (unit-testable pure functions)

BUGGY = """fn withdraw(balance: int, amount: int) -> int
  requires amount > 0
  requires amount <= balance
  ensures result == balance - amount
{
  return balance - amount + 1;
}
"""

FIXED = BUGGY.replace("return balance - amount + 1;", "return balance - amount;")


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------

class TestMapping(unittest.TestCase):
    def test_fn_header_line(self):
        lines = BUGGY.splitlines()
        self.assertEqual(ward_lsp._fn_header_line(lines, "withdraw"), 0)
        self.assertIsNone(ward_lsp._fn_header_line(lines, "nope"))

    def test_extern_fn_header_line(self):
        lines = ['extern fn charge(amount: int) -> int;', '', 'fn main() -> int {']
        self.assertEqual(ward_lsp._fn_header_line(lines, "charge"), 0)
        self.assertEqual(ward_lsp._fn_header_line(lines, "main"), 2)

    def test_clause_line_ensures(self):
        lines = BUGGY.splitlines()
        header = ward_lsp._fn_header_line(lines, "withdraw")
        self.assertEqual(ward_lsp._clause_line(lines, header, "ensures"), 3)
        self.assertEqual(ward_lsp._clause_line(lines, header, "requires"), 1)
        self.assertIsNone(ward_lsp._clause_line(lines, header, "body"))

    def test_range_for_points_at_ensures(self):
        triple = {"location": {"fn": "withdraw", "clause": "ensures"},
                  "violated_obligation": "postcondition of withdraw",
                  "counterexample": {"amount": "1", "balance": "1", "result": "1"}}
        rng = ward_lsp._range_for(triple, BUGGY)
        self.assertEqual(rng["start"]["line"], 3)
        self.assertGreater(rng["end"]["character"], 0)

    def test_range_for_unknown_fn_falls_back_to_line_0(self):
        triple = {"location": {"fn": "ghost", "clause": "ensures"}}
        rng = ward_lsp._range_for(triple, BUGGY)
        self.assertEqual(rng["start"]["line"], 0)

    def test_uri_to_path_windows(self):
        p = ward_lsp.uri_to_path("file:///C:/Users/Legion/x.ward0")
        self.assertEqual(p, Path("C:/Users/Legion/x.ward0"))

    def test_uri_to_path_percent_encoded(self):
        p = ward_lsp.uri_to_path("file:///c%3A/Users/x.ward0")
        self.assertEqual(p, Path("c:/Users/x.ward0"))

    def test_triple_diag_message_contains_counterexample(self):
        triple = {"kind": "postcondition",
                  "location": {"fn": "withdraw", "clause": "ensures"},
                  "violated_obligation": "postcondition of withdraw",
                  "counterexample": {"amount": "1", "balance": "1", "result": "1"}}
        d = ward_lsp._triple_diag(triple, BUGGY)
        self.assertEqual(d["severity"], 1)
        self.assertIn("counterexample", d["message"])
        self.assertIn("amount=1", d["message"])


# ---------------------------------------------------------------------------
# integration test (real server over stdio)
# ---------------------------------------------------------------------------

class LspClient:
    def __init__(self, python: str):
        self.proc = subprocess.Popen(
            [python, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(REPO))
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        while True:
            msg = self._read_one()
            self.q.put(msg)
            if msg is None:
                return

    def _read_one(self) -> dict | None:
        length: int | None = None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        if length is None:
            return None
        body = self.proc.stdout.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.proc.stdin.flush()

    def read(self, timeout: float = 180) -> dict | None:
        return self.q.get(timeout=timeout)

    def wait_publish(self, uri: str, timeout: float = 180) -> dict:
        while True:
            msg = self.read(timeout)
            assert msg is not None, "server closed before publishing diagnostics"
            if (msg.get("method") == "textDocument/publishDiagnostics"
                    and msg["params"]["uri"] == uri):
                return msg

    def close(self) -> int:
        self.send({"jsonrpc": "2.0", "id": 99, "method": "shutdown"})
        self.read(30)
        self.send({"jsonrpc": "2.0", "method": "exit"})
        try:
            return self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return -1


@unittest.skipUnless(SERVER.is_file(), "ward_lsp.py not present")
class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = LspClient(sys.executable)
        cls.client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"processId": None, "rootUri": None,
                                    "capabilities": {}}})
        init = cls.client.read(60)
        assert init is not None and "result" in init, f"initialize failed: {init}"
        cls.caps = init["result"].get("capabilities", {})

    @classmethod
    def tearDownClass(cls):
        rc = cls.client.close()
        cls.exit_code = rc

    def test_initialize_capabilities(self):
        sync = self.caps.get("textDocumentSync", {})
        self.assertTrue(sync.get("save"))
        self.assertEqual(sync.get("change"), 0)

    def test_didOpen_buggy_publishes_counterexample(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bug.ward0"
            path.write_text(BUGGY, encoding="utf-8")
            uri = path.as_uri()
            self.client.send({"jsonrpc": "2.0", "method": "textDocument/didOpen",
                              "params": {"textDocument": {"uri": uri,
                                                          "languageId": "ward0",
                                                          "version": 1,
                                                          "text": BUGGY}}})
            msg = self.client.wait_publish(uri)
            diags = msg["params"]["diagnostics"]
            self.assertTrue(diags, "expected at least one diagnostic")
            joined = " ".join(d["message"] for d in diags)
            self.assertIn("counterexample", joined)
            self.assertIn("amount=1", joined)
            # the squiggle should sit on the ensures clause line
            self.assertIn(3, [d["range"]["start"]["line"] for d in diags])

    def test_didSave_fixed_publishes_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ok.ward0"
            path.write_text(FIXED, encoding="utf-8")
            uri = path.as_uri()
            self.client.send({"jsonrpc": "2.0", "method": "textDocument/didSave",
                              "params": {"textDocument": {"uri": uri}}})
            msg = self.client.wait_publish(uri)
            self.assertEqual(msg["params"]["diagnostics"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
