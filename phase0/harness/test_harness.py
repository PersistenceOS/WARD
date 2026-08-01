"""Smoke tests for the harness (no external model required)."""

import http.server
import json
import threading
import unittest
from pathlib import Path

from harness.dafny_runner import DafnyRunner
from harness.evaluate import evaluate_task, load_tasks, run_experiment
from harness.models import ApiModel, FakeModel, _strip_fences, make_prompt, signature_of

TASKS = Path(__file__).resolve().parent.parent / "benchmarks" / "tasks"

ABS_REF = (TASKS / "t1_abs.ward0").read_text(encoding="utf-8")
ABS_DESC = json.loads((TASKS / "t1_abs.json").read_text(encoding="utf-8"))

RUNNER = DafnyRunner()


class TestSignature(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(signature_of(ABS_REF), "fn abs(x: int) -> int")

    def test_prompt_contains_signature_and_spec(self):
        p = make_prompt(ABS_DESC, ABS_REF)
        self.assertIn("fn abs(x: int) -> int", p)
        self.assertIn("absolute value", p)


class TestStripFences(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_strip_fences("fn f() -> int { return 1; }"), "fn f() -> int { return 1; }")

    def test_fenced(self):
        src = "fn f() -> int { return 1; }"
        self.assertEqual(_strip_fences(f"```ward0\n{src}\n```"), src)

    def test_with_preamble(self):
        src = "fn f() -> int { return 1; }"
        self.assertEqual(_strip_fences(f"Here it is:\n```\n{src}\n```"), src)


class TestDafnyRunner(unittest.TestCase):
    def test_verify_good_source(self):
        ok, detail = RUNNER.verify(ABS_REF)
        self.assertTrue(ok, detail)

    def test_verify_garbage(self):
        ok, _ = RUNNER.verify("this is not ward0")
        self.assertFalse(ok)

    def test_hidden_tests_pass_for_reference(self):
        results = RUNNER.run_hidden_tests(ABS_DESC, ABS_REF)
        self.assertEqual(len(results), len(ABS_DESC["hidden_tests"]))
        self.assertTrue(all(results))

    def test_hidden_tests_fail_for_wrong_impl(self):
        wrong = "fn abs(x: int) -> int { return x + 1; }"
        results = RUNNER.run_hidden_tests(ABS_DESC, wrong)
        self.assertFalse(all(results))


class TestApiModel(unittest.TestCase):
    def test_generate_parses_completion(self):
        served = {"choices": [{"message": {"content": "fn f() -> int { return 1; }"}}]}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(served).encode())

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            model = ApiModel(base_url=f"http://127.0.0.1:{port}/v1", model="test")
            out = model.generate("spec", "t1_abs", 1)
            self.assertEqual(out, "fn f() -> int { return 1; }")
        finally:
            server.shutdown()


class TestEvaluatePipeline(unittest.TestCase):
    def test_evaluate_task_with_fake_model(self):
        model = FakeModel({ABS_DESC["id"]: ABS_REF})
        res = evaluate_task(RUNNER, model, ABS_DESC, ABS_REF, attempts=3)
        self.assertTrue(res.solved)
        self.assertEqual(res.solved_at, 1)
        self.assertEqual(res.attempts[0]["status"], "pass")

    def test_evaluate_task_fake_model_verify_fail_then_pass(self):
        fail = "fn abs(x: int) -> int { return x; }"  # verifies but fails hidden tests

        class FlipModel(FakeModel):
            def generate(self, spec, task_id, attempt):
                return fail if attempt == 1 else ABS_REF

        res = evaluate_task(RUNNER, FlipModel({}), ABS_DESC, ABS_REF, attempts=2)
        self.assertTrue(res.solved)
        self.assertEqual(res.solved_at, 2)
        self.assertEqual(res.attempts[0]["status"], "test_fail")

    def test_run_experiment_subset(self):
        sources = {d["id"]: (TASKS / f"{d['id']}.ward0").read_text(encoding="utf-8") for d in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(TASKS.glob("*.json")))}
        results = run_experiment(RUNNER, FakeModel(sources), TASKS, attempts=1, tiers={1, 2}, limit=4)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.solved for r in results))


if __name__ == "__main__":
    unittest.main()
