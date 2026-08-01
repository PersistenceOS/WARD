"""Model adapters for the ward0 evaluation pipeline.

- FakeModel: returns canned sources (reference solutions) — used for pipeline
  smoke tests and as an oracle sanity check.
- ApiModel: any OpenAI-compatible chat completions endpoint (llama.cpp server,
  vLLM, OpenAI, ...). For local GGUF inference, run e.g.
  `llama-server -m qwen2.5-coder-14b-instruct-q4_k_m.gguf -c 4096 --port 8080`
  and point base_url at `http://localhost:8080/v1`.
"""

import json
import re
import shutil
import subprocess
import urllib.request

from harness.wallclock import WallClockTimeoutError, run_capped

WARD0_GUIDE = """\
You are writing code in ward0, a small verified language. ward0 is a Python/TS-shaped
subset that deterministically elaborates into Dafny; Dafny must be able to prove your
contracts, so write the strongest contracts you can prove.

Language rules:
- A program is exactly one `fn` definition. No classes, no closures, no recursion,
  no `while`, no lists construction (lists are read-only inputs).
- Types: `int`, `bool`, `str`, `Unit`, `Result<T, E>`, `List<T>`.
- Syntax is TS-shaped and REQUIRES semicolons after every STATEMENT in the body.
- Parameters are mutable; assignment to a parameter is allowed (`n += 1;`).
- Arithmetic: + - * / % and unary -; comparisons < <= > >= == !=; boolean
  operators `and` `or` `not` (use `not (e)` with parentheses).
- Integer division `/` and `%` are Euclidean (floor): -10 / 3 == -4,
  -10 % 3 == 2. Note the implementation of `%` on negative operands.
- `if` / `else` blocks use braces; `for i in range(lo, hi)` is the only loop
  (lo <= hi must hold). The loop body is braced.
- To prove anything about a loop-carried variable, add an `invariant` clause
  after the `for` header, e.g.
      for i in range(0, len(xs))
        invariant total >= 0
      {
  - You may use `range(0, len(xs) - 1)` etc. but guard empty ranges first.
  - Early `return` inside a loop avoids needing invariants for that path.
- Contracts: `requires` / `ensures` lines between signature and `{`. Contracts
  are NOT statements: they take NO trailing semicolon — the `{` follows the last
  contract line directly.
  - `result` names the return value.
  - Quantified claims: `forall i in range(lo, hi) :: expr` and
    `exists i in range(lo, hi) :: expr` (both may nest; body in parens).
  - Implication is NOT available: write `not A or B`.
  - Forbidden tokens that never appear in ward0: `**`, `==>`, `&&`, `||`,
    `if`-in-expression (ternary).
- Builtins: `len(xs)` (list length); on a `Result`: `is_ok(r)`, `is_err(r)`,
  `unwrap_ok(r)`, `unwrap_err(r)`.
- `Result<T, E>` values: `Ok(value)` or `Err("message")`.
- Single quotes are not allowed; use double quotes for strings.

Example (a correct, verifiable program):
fn max_of_list(xs: List<int>) -> int
  requires len(xs) > 0
  ensures result >= xs[0]
{
    var m: int = xs[0];
    for i in range(1, len(xs))
      invariant m >= xs[0]
    {
        if xs[i] > m {
            m = xs[i];
        }
    }
    return m;
}

Write ONLY the ward0 source of the requested function. No markdown fences, no
explanation, no tests, no comments.
"""

DAFNY_GUIDE = """\
You are writing Dafny 4 code that must verify with `dafny verify`.

Rules:
- Write a single top-level `method` (no module, no class, no imports).
- Use `requires` / `ensures` contracts; recursion and `decreases` clauses are
  allowed; `while` loops are allowed (with invariants).
- If the method uses Result, first declare:
      datatype Result<T, E> = Ok(value: T) | Err(error: E)
- Integers are unbounded. `/` and `%` are Euclidean (floor): -10 / 3 == -4,
  -10 % 3 == 2.
- Sequence access `xs[i]` requires the index to be provably in bounds; use
  `|xs|` for length.
- List inputs and outputs are Dafny sequences: type them `seq<int>` etc.
  Never define a datatype for lists (e.g. `Nil | Cons` is forbidden).
- Match on datatypes with `match x { case Ok(v) => ... case Err(e) => ... }`.
- Only code, no markdown fences, no explanation, no tests, no comments.
"""


class ModelAdapter:
    def generate(self, spec: str, task_id: str, attempt: int) -> str:
        raise NotImplementedError


class FakeModel(ModelAdapter):
    """Returns a fixed source per task (used for smoke tests / oracle runs)."""

    def __init__(self, sources: dict[str, str], fail_ids: set[str] | None = None):
        self.sources = sources
        self.fail_ids = fail_ids or set()

    def generate(self, spec: str, task_id: str, attempt: int) -> str:
        if task_id in self.fail_ids:
            raise RuntimeError("fake model failure (test hook)")
        return self.sources[task_id]


def _strip_fences(content: str) -> str:
    content = content.strip()
    if "```" in content:
        content = content[content.index("```") + 3 :]
        if content.startswith("\n"):
            content = content[1:]
        elif "ward0\n" in content[:12]:
            content = content.split("\n", 1)[1]
        if "```" in content:
            content = content[: content.index("```")]
    return content.strip()


class OpenCodeModel(ModelAdapter):
    """Runs the prompt through the local `opencode run` CLI (headless mode)."""

    # timeout: normal generations measure 10-22s (Phase-0 logs); a hung endpoint
    # must clip fast instead of burning 900s per attempt (observed 2026-08-01).
    def __init__(self, model: str = "opencode/deepseek-v4-flash-free", timeout: int = 180, guide: str = WARD0_GUIDE):
        self.model = model
        self.timeout = timeout
        self.guide = guide
        self.opencode = shutil.which("opencode") or "opencode"

    def generate(self, spec: str, task_id: str, attempt: int) -> str:
        message = f"{self.guide}\n\n{spec}"
        try:
            proc = run_capped(
                [self.opencode, "run", "--format", "json", "--model", self.model, message],
                timeout=self.timeout,
                capture_output=True,  # REQUIRED: parse loop reads proc.stdout below
                stdin=subprocess.DEVNULL,  # opencode blocks on an open stdin pipe (observed 2026-08-01)
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except WallClockTimeoutError as exc:
            raise RuntimeError(
                f"model generation timed out after {self.timeout}s (process tree killed): {exc}"
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(f"opencode run failed (rc={proc.returncode}): {proc.stderr[:500]}")
        text_parts = []
        for line in proc.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") in ("text", "message.part.updated"):
                part = ev.get("part", {})
                if part.get("type") == "text" and part.get("text"):
                    text_parts.append(part["text"])
        content = "".join(text_parts).strip()
        if not content:
            raise RuntimeError(f"no text content in opencode output; stderr: {proc.stderr[:300]}")
        return _strip_fences(content)


class ApiModel(ModelAdapter):
    """OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 300,
        max_attempts_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts_retries = max_attempts_retries

    def generate(self, spec: str, task_id: str, attempt: int) -> str:
        last_error: Exception | None = None
        for _ in range(self.max_attempts_retries):
            body = json.dumps(
                {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": WARD0_GUIDE},
                        {"role": "user", "content": spec},
                    ],
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
            ).encode()
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                content = data["choices"][0]["message"]["content"]
                return _strip_fences(content)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"model request failed after retries: {last_error}")


def signature_of(reference_source: str) -> str:
    """Extract `fn name(params) -> ret` from a reference ward0 file."""
    for line in reference_source.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("fn ") and "{" not in line:
            return line
        raise ValueError(f"reference source does not start with a signature line (got {line!r})")
    raise ValueError("reference source contains no signature")


def make_prompt(task_desc: dict, reference_source: str) -> str:
    """Build the user prompt for a task."""
    return (
        "Implement the following function in ward0.\n\n"
        f"Signature: {signature_of(reference_source)}\n\n"
        f"Specification:\n{task_desc['spec']}\n\n"
        "Write the ward0 source of the function, including requires/ensures "
        "contracts that Dafny can prove. Only code, no markdown fences, no "
        "explanation."
    )


def make_b_prompt(task_desc: dict, reference_source: str, arm: str, enforce: bool = False) -> str:
    """Build the experiment-B prompt (trust: contract shown; baseline: signature only)."""
    stub = task_desc["extern"]
    params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
    stub_text = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
    if arm == "trust" and stub.get("contract"):
        stub_text += "\n" + stub["contract"]
    stub_text += ";"
    lines = [
        "Implement the following function in ward0.",
        "",
        f"Signature: {signature_of(reference_source)}",
        "",
        "The harness provides this library stub. You write exactly one fn "
        "definition; the harness prepends the stub declaration itself, so do "
        "not repeat it in your answer:",
        stub_text,
        "",
    ]
    if arm == "trust":
        lines.append(
            "The stub declaration above carries its full contract; the verifier "
            "treats it as an axiom, so you can prove properties of calls to it. "
            "Note: the contract is a claim about the stub, and its real runtime "
            "behavior may differ from it."
        )
    else:
        lines.append(
            "The stub declaration above has no contract: the verifier knows "
            "nothing about the stub's behavior."
        )
    lines += [
        'If a call\'s result contradicts the documented behavior, the caller must return Err("contract violation").',
        "",
        f"Specification:\n{task_desc['spec']}",
        "",
    ]
    if enforce:
        lines += [
            "The toolchain wraps every stub call in an auto-generated runtime "
            'check: if the stub\'s actual result contradicts the contract above, the '
            'call itself returns Err("contract violation") instead of the stub\'s '
            'value. Treat that as the true result — your function MUST NOT second-'
            "guess, re-label, or fabricate error strings based on inspecting the "
            "result; simply return the result you received. You still verify the "
            "contract of the result you return, but you never re-derive the "
            "stub's outcome yourself.",
        ]
    lines += [
        "Write the ward0 source of the function, including requires/ensures "
        "contracts that Dafny can prove. Only code, no markdown fences, no "
        "explanation.",
    ]
    return "\n".join(lines)


def dafny_signature_of(reference_source: str) -> str:
    """Convert a ward0 signature line to its Dafny form (List<T> -> seq<T>)."""
    sig = signature_of(reference_source)
    head, tail = sig.split("(", 1)
    name = head.removeprefix("fn ").strip()
    params, ret = tail.rsplit("->", 1)
    params = re.sub(r"List<([^>]+)>", r"seq<\1>", params.strip())
    ret = re.sub(r"List<([^>]+)>", r"seq<\1>", ret.strip())
    if params.endswith(")"):
        params = params[:-1]
    return f"method {name}({params}) returns (result: {ret})"


def clean_dafny(candidate: str) -> str:
    """Strip fences plus leading `dafny`-echo noise from a raw-Dafny candidate."""
    content = _strip_fences(candidate)
    lines = content.splitlines()
    while lines and lines[0].strip().lower() in ("dafny", "```dafny"):
        lines.pop(0)
    return "\n".join(lines).strip()


def make_raw_prompt(task_desc: dict, reference_source: str) -> str:
    """Build the raw-Dafny prompt for a task (no ward0 involvement)."""
    return (
        "Implement the following function in Dafny.\n\n"
        f"Signature: {dafny_signature_of(reference_source)}\n\n"
        f"Specification:\n{task_desc['spec']}\n\n"
        "Write the Dafny source of the method, including requires/ensures "
        "contracts that `dafny verify` can prove. Only code, no markdown "
        "fences, no explanation."
    )
