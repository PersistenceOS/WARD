"""Dafny verification and hidden-test execution for ward0 programs."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from harness.wallclock import WallClockTimeoutError, run_capped
from transpiler.transpiler import Ward0Transpiler

VERIFY_TIME_LIMIT = "30"

_RUNTIME_CANDIDATES = [
    Path.home() / ".dotnet" / "tools" / ".store" / "dafny",
    Path.home() / ".nuget" / "packages",
]


def _find_python_runtime() -> str | None:
    """Locate DafnyRuntimePython (contains the `_dafny` package) from the dafny install."""
    for root in _RUNTIME_CANDIDATES:
        if not root.is_dir():
            continue
        for path in root.rglob("DafnyRuntimePython"):
            if (path / "_dafny" / "__init__.py").is_file():
                return str(path)
    return None


def find_z3() -> str | None:
    z3 = shutil.which("z3")
    if z3:
        return z3
    z3_dir = Path.home() / ".z3"
    if z3_dir.is_dir():
        for exe in z3_dir.glob("*/bin/z3.exe"):
            return str(exe)
    return None


def _literal(value) -> str:
    """Render a JSON test value as a Dafny expression."""
    if value is None:
        return "()"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        return "[" + ", ".join(_literal(x) for x in value) + "]"
    if isinstance(value, dict):
        if "ok" in value:
            return "Ok(" + _literal(value["ok"]) + ")"
        if "err" in value:
            return "Err(" + _literal(value["err"]) + ")"
    raise ValueError(f"cannot render test value {value!r}")


class DafnyRunner:
    """Wraps the ward0 transpiler + `dafny verify` + compiled hidden tests."""

    def __init__(self, dafny: str | None = None, z3: str | None = None, verify_limit: int = 30):
        self.dafny = shutil.which("dafny") if dafny is None else dafny
        if not self.dafny:
            raise RuntimeError("dafny executable not found on PATH")
        self.z3 = z3 if z3 is not None else find_z3()
        self.verify_limit = verify_limit
        self.transpiler = Ward0Transpiler()
        self.py_runtime = _find_python_runtime()

    def _base_cmd(self, verify_limit: int | None = None) -> list[str]:
        limit = self.verify_limit if verify_limit is None else verify_limit
        cmd = [self.dafny, "verify", f"--verification-time-limit:{limit}"]
        if self.z3:
            cmd += ["--solver-path", self.z3]
        return cmd

    def transpile(self, ward0_src: str, enforce: bool = False) -> str:
        self.transpiler.enforce_boundary = enforce
        try:
            return self.transpiler.transpile(ward0_src)
        finally:
            self.transpiler.enforce_boundary = False

    def verify(self, ward0_src: str, timeout: int = 120, enforce: bool = False, verify_limit: int | None = None) -> tuple[bool, str]:
        """Verify a ward0 source against Dafny. Returns (ok, detail)."""
        try:
            dafny_src = self.transpile(ward0_src, enforce=enforce)
        except Exception as exc:  # TranspileError
            return False, f"transpile error: {exc}"
        with tempfile.TemporaryDirectory() as td:
            dfy = Path(td) / "task.dfy"
            dfy.write_text(dafny_src, encoding="utf-8")
            try:
                proc = run_capped(
                    self._base_cmd(verify_limit) + [str(dfy)],
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                )
            except WallClockTimeoutError as exc:
                # A timeout is a failed verification, not a run error: return
                # (False, detail) so the Contracted-tier test fallback still
                # runs (an exception would skip the else-branch in the eval
                # drivers and defeat the bounded-proof design).
                return False, f"verify wall-clock timeout after {timeout}s (process tree killed): {exc}"
        detail = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, detail

    def verify_dafny(self, dafny_src: str, timeout: int = 120, verify_limit: int | None = None) -> tuple[bool, str]:
        """Verify raw Dafny source (no transpilation). Returns (ok, detail)."""
        with tempfile.TemporaryDirectory() as td:
            dfy = Path(td) / "task.dfy"
            dfy.write_text(dafny_src, encoding="utf-8")
            try:
                proc = run_capped(
                    self._base_cmd(verify_limit) + [str(dfy)],
                    timeout=timeout,
                    capture_output=True,
                    text=True,
                )
            except WallClockTimeoutError as exc:
                return False, f"verify wall-clock timeout after {timeout}s (process tree killed): {exc}"
        detail = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, detail

    @staticmethod
    def _build_main(task_desc: dict, fn_name: str) -> str:
        tests = task_desc["hidden_tests"]
        lines = []
        for idx, case in enumerate(tests):
            args = ", ".join(_literal(a) for a in case["in"])
            lines.append(f"    var r{idx} := {fn_name}({args});")
            lines.append(f"    expect r{idx} == {_literal(case['out'])};")
        assertions = "\n".join(lines)
        return (
            "method Main() {\n"
            f"{assertions}\n"
            '    print "ALL HIDDEN TESTS PASSED\\n";\n'
            "}\n"
        )

    def _run_compiled(self, dafny_src: str, main: str, timeout: int) -> bool:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            dfy = td / "task.dfy"
            dfy.write_text(dafny_src + "\n" + main, encoding="utf-8")
            out_dir = td / "prog"
            proc = run_capped(
                [self.dafny, "translate", "py", "--output", str(out_dir), str(dfy)],
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"dafny translate failed:\n{proc.stdout}{proc.stderr}")
            entry = next((p for p in out_dir.parent.glob(f"{out_dir.name}-py/*") if p.name == "__main__.py"), None)
            if entry is None:
                raise RuntimeError("dafny translate produced no __main__.py")
            env = dict(os.environ)
            if self.py_runtime:
                env["PYTHONPATH"] = self.py_runtime + os.pathsep + env.get("PYTHONPATH", "")
            run = run_capped(
                [sys.executable, str(entry)],
                timeout=timeout,
                cwd=str(entry.parent),
                capture_output=True,
                text=True,
                env=env,
            )
        return run.returncode == 0

    def run_hidden_tests(self, task_desc: dict, ward0_src: str, timeout: int = 180) -> list[bool]:
        """Compile ward0 + a Main() of `expect` assertions and run the hidden tests.

        Returns one boolean per hidden test (True = passed).
        """
        dafny_src = self.transpiler.transpile(ward0_src)
        fn_name = next(
            (line.split("(")[0].replace("method ", "").strip() for line in dafny_src.splitlines() if line.startswith("method ")),
            None,
        )
        if fn_name is None:
            raise RuntimeError("no method found in transpiled source")
        main = self._build_main(task_desc, fn_name)
        if self._run_compiled(dafny_src, main, timeout):
            return [True] * len(task_desc["hidden_tests"])
        return [False] * len(task_desc["hidden_tests"])

    def run_hidden_tests_dafny(self, task_desc: dict, dafny_src: str, timeout: int = 180) -> list[bool]:
        """Compile raw Dafny + hidden-test Main and run it (no transpilation)."""
        fn_name = next(
            (line.split("(")[0].replace("method ", "").strip() for line in dafny_src.splitlines() if line.startswith("method ")),
            None,
        )
        if fn_name is None:
            raise RuntimeError("no method found in Dafny source")
        main = self._build_main(task_desc, fn_name)
        if self._run_compiled(dafny_src, main, timeout):
            return [True] * len(task_desc["hidden_tests"])
        return [False] * len(task_desc["hidden_tests"])

    # ------------------------------------------------------------ experiment B / Phase 1

    @staticmethod
    def externs_of(task_desc: dict) -> list[dict]:
        """Normalize extern declarations: single `extern` (b_tasks) or `externs` list (w_tasks)."""
        ext = task_desc.get("extern")
        if ext is not None:
            return ext if isinstance(ext, list) else [ext]
        return list(task_desc.get("externs", []))

    @staticmethod
    def extern_ward0_of(task_desc: dict, arm: str) -> str:
        """Render the extern stub declaration(s) in ward0 (trust arm adds contracts)."""
        parts = []
        for stub in DafnyRunner.externs_of(task_desc):
            params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
            sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
            if arm == "trust" and stub.get("contract"):
                sig += "\n  " + stub["contract"]
            parts.append(sig + ";")
        return "\n\n".join(parts)

    @staticmethod
    def _caller_fn_name(ward0_src: str) -> str:
        for line in ward0_src.splitlines():
            line = line.strip()
            if line.startswith("fn "):
                return line.split("(")[0].removeprefix("fn ").strip()
        raise RuntimeError("no fn definition found in ward0 source")

    def verify_b(self, ward0_src: str, task_desc: dict, arm: str, timeout: int = 120, enforce: bool = False, verify_limit: int | None = None) -> tuple[bool, str]:
        """Verify a B/w caller: extern stub(s) (prepended) + caller fn(s)."""
        src = self.extern_ward0_of(task_desc, arm) + "\n\n" + ward0_src
        return self.verify(src, timeout, enforce=enforce, verify_limit=verify_limit)

    @staticmethod
    def _build_main_b(task_desc: dict, fn_name: str) -> str:
        """Main that prints one `CASEi=MARK` per hidden test, MARK in {PASS, OKLEAK, ERRFAIL}.

        PASS = output matched the expected literal; OKLEAK = output was Ok but the
        case expected an Err (a boundary escape — an over-grant crossed to the
        caller); ERRFAIL = output was some Err that did not match (boundary-safe
        caller-logic failure).
        """
        tests = task_desc["hidden_tests"]
        lines = []
        for idx, case in enumerate(tests):
            args = ", ".join(_literal(a) for a in case["in"])
            lines.append(f"    var r{idx} := {fn_name}({args});")
            lines.append(f'    var m{idx} := if r{idx} == {_literal(case["out"])} then "PASS" else (if r{idx}.Ok? then "OKLEAK" else "ERRFAIL");')
            lines.append(f'    print "CASE{idx}=" + m{idx} + "\\n";')
        lines.append('    print "ALL HIDDEN TESTS DONE\\n";')
        return "method Main() {\n" + "\n".join(lines) + "\n}\n"

    @staticmethod
    def _stub_injection(task_desc: dict) -> str:
        """Python code appended to the compiled module_.py: real stub(s) + adapters.

        Each stub impl works on plain Python values (("ok", v) | ("err", s));
        the adapter converts to the Dafny-compiled representations.
        """
        out = []
        for stub in DafnyRunner.externs_of(task_desc):
            name = stub["name"]
            mangled = name.replace("_", "__")
            str_params = [p[0] for p in stub["params"] if p[1] == "str"]
            if str_params:
                conv = (
                    "def _dafny_str_of(x):\n"
                    "    return x.VerbatimString(False)\n"
                )
                args = ", ".join(
                    f"_dafny_str_of({p})" if p in str_params else p for p, _ in stub["params"]
                )
            else:
                conv = ""
                args = ", ".join(p for p, _ in stub["params"])
            out.append(
                conv
                + "def _stub_out(out):\n"
                "    kind, val = out\n"
                "    if val is None:\n"
                "        val = ()\n"
                '    if kind == "ok":\n'
                "        return Result_Ok(val)\n"
                "    return Result_Err(_dafny_str(val))\n"
                "\n"
                "def _dafny_str(s):\n"
                "    return _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, s))\n"
                "\n"
                f"def _adapt_{name}({', '.join(p for p, _ in stub['params'])}):\n"
                f"    return _stub_out({name}_stub({args}))\n"
                "\n"
                f"default__.{mangled} = staticmethod(_adapt_{name})\n"
            )
        return "\n".join(out)

    def _run_compiled_b(self, dafny_src: str, main: str, task_desc: dict, timeout: int, no_verify: bool = False) -> str:
        """Translate to Python (optionally skipping verification), inject the stub
        implementation(s), run, return stdout."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            dfy = td / "task.dfy"
            dfy.write_text(dafny_src + "\n" + main, encoding="utf-8")
            out_dir = td / "prog"
            cmd = [self.dafny, "translate", "py", "--output", str(out_dir)]
            if no_verify:
                cmd.append("--no-verify")
            cmd.append(str(dfy))
            proc = run_capped(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"dafny translate failed:\n{proc.stdout}{proc.stderr}")
            prog_py = out_dir.parent / f"{out_dir.name}-py"
            entry = prog_py / "__main__.py"
            if not entry.is_file():
                raise RuntimeError("dafny translate produced no __main__.py")
            module_py = prog_py / "module_.py"
            impls = "\n".join(s["impl"] for s in DafnyRunner.externs_of(task_desc))
            with module_py.open("a", encoding="utf-8") as fh:
                fh.write("\n" + impls + "\n" + self._stub_injection(task_desc))
            env = dict(os.environ)
            path = str(prog_py)
            if self.py_runtime:
                path += os.pathsep + self.py_runtime
            if env.get("PYTHONPATH"):
                path += os.pathsep + env["PYTHONPATH"]
            env["PYTHONPATH"] = path
            run = run_capped(
                [sys.executable, str(entry)],
                timeout=timeout,
                cwd=str(prog_py),
                capture_output=True,
                text=True,
                env=env,
            )
            if run.returncode != 0:
                return ""
            return run.stdout

    def run_hidden_tests_b(self, task_desc: dict, ward0_src: str, arm: str, timeout: int = 180, enforce: bool = False) -> list[bool]:
        """Compile extern + caller + marker Main, inject the stub, run, parse markers.

        Returns one boolean per hidden test (True = the caller's output matched).
        """
        dafny_src = self.transpile(self.extern_ward0_of(task_desc, arm) + "\n\n" + ward0_src, enforce=enforce)
        fn_name = self._caller_fn_name(ward0_src)
        main = self._build_main_b(task_desc, fn_name)
        stdout = self._run_compiled_b(dafny_src, main, task_desc, timeout)
        results = []
        for idx, _case in enumerate(task_desc["hidden_tests"]):
            results.append(f"CASE{idx}=PASS" in stdout)
        return results

    def run_hidden_tests_b_marked(self, task_desc: dict, ward0_src: str, arm: str, timeout: int = 180, enforce: bool = False, no_verify: bool = False) -> list[str]:
        """Like run_hidden_tests_b but returns the per-case marker (PASS|OKLEAK|ERRFAIL)."""
        dafny_src = self.transpile(self.extern_ward0_of(task_desc, arm) + "\n\n" + ward0_src, enforce=enforce)
        fn_name = self._caller_fn_name(ward0_src)
        main = self._build_main_b(task_desc, fn_name)
        stdout = self._run_compiled_b(dafny_src, main, task_desc, timeout, no_verify=no_verify)
        markers = []
        for idx, _case in enumerate(task_desc["hidden_tests"]):
            for marker in ("PASS", "OKLEAK", "ERRFAIL"):
                if f"CASE{idx}={marker}" in stdout:
                    markers.append(marker)
                    break
            else:
                markers.append("NOOUT")
        return markers

    def run_hidden_tests_dafny_b_marked(self, task_desc: dict, dafny_caller: str, timeout: int = 180, no_verify: bool = False) -> list[str]:
        """Raw-Dafny arm: prepend hand-authored extern declarations (`extern_dafny`
        field), build the marker Main, inject stubs, run."""
        ext = task_desc.get("extern_dafny", "")
        src = ext + "\n\n" + dafny_caller if ext else dafny_caller
        fn_name = next(
            (line.split("(")[0].replace("method ", "").strip() for line in dafny_caller.splitlines() if line.startswith("method ")),
            None,
        )
        if fn_name is None:
            raise RuntimeError("no method found in Dafny caller")
        main = self._build_main_b(task_desc, fn_name)
        stdout = self._run_compiled_b(src, main, task_desc, timeout, no_verify=no_verify)
        markers = []
        for idx, _case in enumerate(task_desc["hidden_tests"]):
            for marker in ("PASS", "OKLEAK", "ERRFAIL"):
                if f"CASE{idx}={marker}" in stdout:
                    markers.append(marker)
                    break
            else:
                markers.append("NOOUT")
        return markers

    @staticmethod
    def check_scenario_sanity(task_desc: dict) -> list[str]:
        """Validate the scenario design: each stub violates exactly its flagged region.

        Legacy b_tasks: probe via hidden tests' `violation` flags. w_tasks: probe via
        each extern's `violation_probes` list ({in, violation}).
        """
        errors = []
        for stub in DafnyRunner.externs_of(task_desc):
            namespace: dict = {}
            exec(stub["impl"], namespace)
            impl_fn = namespace[stub["name"] + "_stub"]
            contract = eval(stub["contract_py"])
            probes = stub.get("violation_probes")
            if probes is None:
                for idx, case in enumerate(task_desc["hidden_tests"]):
                    out = impl_fn(*case["in"])
                    holds = contract(*case["in"], out=out)
                    want_violation = bool(case.get("violation", False))
                    if holds == want_violation:
                        errors.append(
                            f"{stub['name']} case {idx}: {'flagged violation but stub complies' if want_violation else 'not flagged but stub violates'}"
                        )
            else:
                for probe in probes:
                    out = impl_fn(*probe["in"])
                    holds = contract(*probe["in"], out=out)
                    want = bool(probe.get("violation", False))
                    if holds == want:
                        errors.append(
                            f"{stub['name']} probe {probe['in']}: {'flagged violation but stub complies' if want else 'not flagged but stub violates'}"
                        )
        return errors
