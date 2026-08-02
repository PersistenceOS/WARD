"""E6 gate: linearity (T7) — files/ward-phase2-scoping.md §6.

Pre-registered E6: "Linear values are consumed exactly once on every path;
probes for copy / drop / double-use all fail; money-transfer oracle passes."

Probes (all through the ward-core elaborator's linearity pass):
  A. copy        — a linear value read in a condition (non-consuming position)
                   -> hard ElaborationError "is copied (T7)"
  B. copy        — a linear value passed to a NON-linear callee param
                   -> hard ElaborationError "is copied (T7)"
  C. drop        — a linear param never consumed on some path
                   -> hard ElaborationError "is dropped (T7)"
  D. double-use  — a linear value consumed twice on a path
                   -> hard ElaborationError "consumed more than once (T7)"
  E. loop        — a linear value used inside a loop
                   -> hard ElaborationError "inside a loop (T7)" (v0.1 boundary)
  F. minting     — a non-linear expression at a linear param
                   -> hard ElaborationError "cannot consume (T7)"
  G. money-transfer oracle PASSES — the reference elaborates, the emitted
     Dafny verifies clean, and hidden tests pass with 0 boundary_okleak under
     enforce-on (the capability param is consumed exactly once on every path).
  H. inference-only — a param that never flows into a linear extern is NOT
     constrained (linearity is inferred, never model-annotated — design doc
     §4/§5): the no-linear-extern control module elaborates clean.
  I. path-split  — a linear value consumed in one branch but not the other
                   -> drop on the other path -> hard error (every path!)

Usage:  python -m wardcore.e6_gate
"""

from __future__ import annotations

import sys

from harness.dafny_runner import DafnyRunner

from wardcore.elaborator import Elaborator, ElaborationError

# The money-transfer oracle: `amount` is the linear capability param of the
# `ledger_debit` extern; `transfer` must consume it exactly once on every path.
MONEY_SRC = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    var r: Result<int, str> = ledger_debit(amount);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}
"""

# Hidden-test descriptor for the money oracle (conforming stub: honours its
# contract exactly, so the `_checked` wrapper must never false-fire).
MONEY_DESC = {
    "id": "e6_money_transfer",
    "fn": "transfer",
    "extern": {
        "name": "ledger_debit",
        "params": [["amount", "int"]],
        "ret": "Result<int, str>",
        "contract": "requires amount > 0\nensures is_ok(result) == (amount <= 1000000)",
        "impl": (
            "def ledger_debit_stub(amount):\n"
            "    if amount <= 1000000:\n"
            "        return (\"ok\", amount)\n"
            "    return (\"err\", \"limit\")\n"
        ),
    },
    "hidden_tests": [
        {"in": [500], "out": {"ok": 500}},
        {"in": [1000000], "out": {"ok": 1000000}},
        {"in": [1000001], "out": {"err": "limit"}},
    ],
}


def _expect_error(src: str, needle: str, label: str) -> bool:
    try:
        Elaborator().transpile(src)
    except ElaborationError as exc:
        ok = needle in str(exc)
        print(f"  probe {label}: {'PASS' if ok else 'FAIL'} -> {str(exc)[:100]}")
        return ok
    print(f"  probe {label}: FAIL (no error raised)")
    return False


def probe_a_copy_in_condition() -> bool:
    # wrap the WHOLE body in `if amount > 0` — the condition reads `amount` in
    # a non-consuming position (the copy), while `r` stays in scope below
    src = MONEY_SRC.replace(
        "{\n    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {\n        return Err(unwrap_err(r));\n    }\n    return Ok(unwrap_ok(r));\n}",
        "{\n    if amount > 0 {\n        var r: Result<int, str> = ledger_debit(amount);\n        if is_err(r) {\n            return Err(unwrap_err(r));\n        }\n        return Ok(unwrap_ok(r));\n    }\n    return Err(\"neg\");\n}",
    )
    return _expect_error(src, "is copied", "A (copy: read in condition = hard error)")


def probe_b_copy_to_nonlinear_param() -> bool:
    # `read_balance` takes a NON-linear param; passing the linear `amount` to
    # it is a copy. The leak must happen in the SAME fn where `amount` is
    # linear (a separate fn's param is never inferred linear — that is the
    # inference-only rule, probe H).
    src = MONEY_SRC.replace(
        "    var r: Result<int, str> = ledger_debit(amount);",
        "    var b: int = read_balance(amount);\n    var r: Result<int, str> = ledger_debit(amount);",
    )
    src = src.replace(
        "fn transfer(",
        "fn read_balance(x: int) -> int\n  ensures result >= 0\n{\n    return x;\n}\n\nfn transfer(",
    )
    return _expect_error(src, "is copied", "B (copy: linear into non-linear param = hard error)")


def probe_c_drop() -> bool:
    src = MONEY_SRC.replace(
        "    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
        "    return Ok(0);\n    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
    )
    return _expect_error(src, "is dropped", "C (drop: linear never consumed = hard error)")


def probe_d_double_use() -> bool:
    src = MONEY_SRC.replace(
        "    return Ok(unwrap_ok(r));",
        "    var r2: Result<int, str> = ledger_debit(amount);\n    return Ok(unwrap_ok(r2));",
    )
    return _expect_error(src, "consumed more than once", "D (double-use = hard error)")


def probe_e_loop() -> bool:
    # `amount` mentioned inside the loop body (any position) is a hard error —
    # consumption count across iterations is not trackable in v0.1
    src = MONEY_SRC.replace(
        "{\n    var r: Result<int, str> = ledger_debit(amount);",
        "{\n    for i in range(0, 3) {\n        var unused: int = amount;\n    }\n    var r: Result<int, str> = ledger_debit(amount);",
    )
    return _expect_error(src, "inside a loop", "E (linear inside loop = hard error, v0.1 boundary)")


def probe_f_minting() -> bool:
    src = MONEY_SRC.replace(
        "    var r: Result<int, str> = ledger_debit(amount);",
        "    var r: Result<int, str> = ledger_debit(500);",
    )
    return _expect_error(src, "cannot consume", "F (non-linear expr at linear param = hard error)")


def probe_g_money_transfer_oracle() -> bool:
    """The reference passes: elaborates, dafny verifies, hidden tests 0 OKLEAK."""
    runner = DafnyRunner()
    elab = Elaborator(enforce_boundary=True)
    try:
        emitted = elab.transpile(MONEY_SRC)
    except ElaborationError as exc:
        print(f"  probe G (money-transfer oracle): FAIL (elaboration error) -> {str(exc)[:120]}")
        return False
    ok, detail = runner.verify_dafny(emitted, timeout=120)
    markers = runner.run_emitted_dafny_b_marked(MONEY_DESC, emitted, "transfer", no_verify=True)
    good = ok and markers == ["PASS"] * len(MONEY_DESC["hidden_tests"])
    print(
        f"  probe G (money-transfer oracle passes): {'PASS' if good else 'FAIL'} "
        f"(elaborated, dafny verify={ok}, hidden markers={markers})"
    )
    if not good:
        print(f"    verify detail: {detail.strip()[:300]}")
    return good


def probe_h_inference_only() -> bool:
    """A param that never reaches a linear extern is NOT constrained (design
    doc §4: linearity is inferred for values flowing through a linear-typed
    capability — the model never annotates)."""
    src = """\
extern fn log_note(msg: int) -> Result<int, str>
  requires msg >= 0
ensures is_ok(result) == (msg < 1000000);
trust: "oracle reference stub"

fn plain(x: int) -> Result<int, str>
  requires x >= 0
  ensures is_ok(result)
{
    var r: Result<int, str> = log_note(x);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}
"""
    elab = Elaborator()
    try:
        elab.transpile(src)
        inferred = elab.linearity_inferred or {}
        ok = "plain" not in inferred or not inferred["plain"]
    except ElaborationError as exc:
        ok = False
        print(f"  probe H (inference-only): FAIL -> {str(exc)[:100]}")
        return ok
    print(f"  probe H (inference-only, no linear extern): {'PASS' if ok else 'FAIL'} (plain param unconstrained)")
    return ok


def probe_i_path_split() -> bool:
    """Consumed in one branch only -> dropped on the other path."""
    src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn conditional(amount: int, go: bool) -> Result<int, str>
  requires amount > 0
{
    if go {
        var r: Result<int, str> = ledger_debit(amount);
        return Ok(unwrap_ok(r));
    }
    return Err("skipped");
}
"""
    return _expect_error(src, "is dropped", "I (path-split: one branch consumes, other drops = hard error)")


def main() -> int:
    print("E6 gate: linearity (T7) — consume-exactly-once")
    results = [
        probe_a_copy_in_condition(),
        probe_b_copy_to_nonlinear_param(),
        probe_c_drop(),
        probe_d_double_use(),
        probe_e_loop(),
        probe_f_minting(),
        probe_g_money_transfer_oracle(),
        probe_h_inference_only(),
        probe_i_path_split(),
    ]
    if all(results):
        print(
            "\nE6 GATE PASS: copy/drop/double-use/loop/minting probes all fail, "
            "money-transfer oracle passes, inference-only control clean, path-split enforced"
        )
        return 0
    print(f"\nE6 GATE FAIL ({results.count(False)}/9 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
