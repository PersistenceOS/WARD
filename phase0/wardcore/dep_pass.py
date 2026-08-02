"""ward-core dependency-resolution pass (Phase-2 week 5) — E4b as a core pass.

Pre-registered scope (files/ward-phase2-scoping.md §2/§3/§4, §6 E4b; design doc
§4 package+version effect vocabulary, §5 resp. 5):

- Every dependency reference is resolved against a declared, pinned version
  range at elaboration time (design doc §5 resp. 5) — a version/schema
  assumption is a checkable compile-time constraint, not a runtime surprise
  (this is the GitChameleon library-version failure mode the design doc
  targets directly).
- **Hard errors, never a silent choice:** a reference whose dependency name
  has no declared range (unresolved), a version outside its declared range
  (version drift), or a name pinned by more than one range (ambiguous) all
  fail elaboration.
- **In-range references pass**; the E4b gate's version-drift probe is the
  oracle.

Surface syntax (mirrors the `trust:`/`tier:`/`effect:` toolchain annotations):

    dep: ledger@^2.0.0        # module header, BEFORE the first def: pins the
                              # allowed range for dependency `ledger`
    extern fn ledger_debit(...)
      requires ...
      ensures ...
    dep: ledger@2.4.1         # AFTER an extern def: this stub is the interface
                              # to `ledger` at version 2.4.1 (the reference)

Resolution semantics:

    declared ranges          Module.deps  (IR field, tuple of "name@range")
    reference per extern     ExternFn.dep (IR field, "name@version")
    resolved                 reference name declared AND version in range
    unresolved               reference name not declared              (E4b)
    out_of_range             version outside the pinned range          (E4b)
    ambiguous                more than one declared range for the name (E4b)

Version grammar (v0.1, deterministic and small — enough for a drift probe):

    X.Y.Z        exact            [X.Y.Z, X.Y.Z]
    ^X.Y.Z       caret            [X.Y.Z, (X+1).0.0)
    ~X.Y.Z       tilde            [X.Y.Z, X.(Y+1).0)
    X.Y.* / X.*  wildcard         [X.Y.0, X.(Y+1).0) / [X.0.0, (X+1).0.0)

Anything else (e.g. `X.Y`, `>=1.0.0 <2.0.0`, a trailing `;`) is a hard
elaboration error — never a silent choice (design doc §5). Deferred range
forms (explicit comparator ranges) land with the registry (design doc §6).

Week-5 boundary (E1 parity): the reference-implies-declared check applies to
externs that CARRY a `dep:` reference. Externs without one are unconstrained —
the 70-reference corpus declares no deps, so parity is untouched. This mirrors
the week-4 effects boundary exactly (scoping doc §7: declared-implies-inferred
check first).

E4b gate (scoping doc §6): "A dependency reference outside its declared
version range (or unresolvable) fails elaboration; in-range reference passes.
Oracle scenario covers a version-drift probe." Gate runner: wardcore/e4b_gate.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from wardcore.ir import Module

Version = tuple[int, int, int]


@dataclass(frozen=True)
class VersionRange:
    """A pinned range `spec`. lo inclusive; hi exclusive (None = unbounded)."""

    spec: str
    lo: Version
    hi: Version | None

    def contains(self, v: Version) -> bool:
        if v < self.lo:
            return False
        if self.hi is not None and not (v < self.hi):
            return False
        return True


def _parse_version(text: str) -> Version:
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise _dep_error(f"malformed version {text.strip()!r} (expected X.Y.Z)")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _dep_error(msg: str) -> Exception:
    # lazy import breaks the elaborator -> dep_pass -> elaborator cycle
    from wardcore.elaborator import ElaborationError

    return ElaborationError(msg)


def parse_range(text: str) -> VersionRange:
    """Parse a pinned range spec. Malformed specs are hard errors."""
    t = text.strip()
    if t.startswith("^"):
        v = _parse_version(t[1:])
        return VersionRange(t, v, (v[0] + 1, 0, 0))
    if t.startswith("~"):
        v = _parse_version(t[1:])
        return VersionRange(t, v, (v[0], v[1] + 1, 0))
    if "*" in t:
        pre = t[: t.index("*")].rstrip(".")
        parts = pre.split(".") if pre else []
        if len(parts) == 1 and parts[0].isdigit():
            m = int(parts[0])
            return VersionRange(t, (m, 0, 0), (m + 1, 0, 0))
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            m, n = int(parts[0]), int(parts[1])
            return VersionRange(t, (m, n, 0), (m, n + 1, 0))
        raise _dep_error(f"malformed range {t!r} (expected X.Y.Z, ^X.Y.Z, ~X.Y.Z, X.Y.*, X.*)")
    v = _parse_version(t)
    return VersionRange(t, v, (v[0], v[1], v[2] + 1))  # exact: [v, next patch)


# ---------------------------------------------------------------------------
# Resolution plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepRecord:
    """One extern's dependency reference and how it resolved (E4b)."""

    extern: str
    dep: str
    version: str
    status: str  # resolved | unresolved | out_of_range | ambiguous
    ranges: tuple[str, ...] = ()  # declared ranges for the referenced name


@dataclass(frozen=True)
class DepResolution:
    records: tuple[DepRecord, ...]

    def for_extern(self, name: str) -> DepRecord:
        for r in self.records:
            if r.extern == name:
                return r
        raise KeyError(f"no dependency record for extern {name!r}")

    @property
    def all_resolved(self) -> bool:
        return all(r.status == "resolved" for r in self.records)


class DepPass:
    """E4b: dependency resolution as a core pass.

    `resolve` computes the per-extern resolution plan (pure, no side effects);
    `validate` reports the E4b problems; `run` = validate (hard error on
    problems) + return the plan for the runner (plan exposure, like
    TierPass/EffectsPass).
    """

    def resolve(self, module: Module) -> DepResolution:
        declared: dict[str, list[str]] = {}
        for d in module.deps:
            if "@" not in d:
                raise _dep_error(f"malformed dependency declaration {d!r} (expected name@range)")
            name, spec = d.split("@", 1)
            parse_range(spec)  # validates; malformed spec is a hard error
            declared.setdefault(name, []).append(spec)

        records: list[DepRecord] = []
        for ext in module.externs:
            if not ext.dep:
                continue  # week-5 boundary: no reference -> unconstrained
            if "@" not in ext.dep:
                raise _dep_error(
                    f"extern {ext.name}: malformed dependency reference {ext.dep!r} "
                    "(expected name@version)"
                )
            name, version = ext.dep.split("@", 1)
            _parse_version(version)  # validates; malformed version is a hard error
            ranges = tuple(declared.get(name, ()))
            if not ranges:
                status = "unresolved"
            elif len(ranges) > 1:
                status = "ambiguous"
            elif not parse_range(ranges[0]).contains(_parse_version(version)):
                status = "out_of_range"
            else:
                status = "resolved"
            records.append(DepRecord(ext.name, name, version, status, ranges))
        return DepResolution(tuple(records))

    def validate(self, module: Module) -> list[str]:
        """E4b problems: unresolved, out-of-range (version drift), ambiguous."""
        problems: list[str] = []
        for r in self.resolve(module).records:
            if r.status == "unresolved":
                problems.append(
                    f"extern {r.extern}: dependency reference {r.dep}@{r.version} "
                    f"is unresolved — no pinned range declared for {r.dep!r} (E4b)"
                )
            elif r.status == "out_of_range":
                problems.append(
                    f"extern {r.extern}: dependency reference {r.dep}@{r.version} "
                    f"is outside the pinned range {r.ranges[0]} (E4b)"
                )
            elif r.status == "ambiguous":
                problems.append(
                    f"extern {r.extern}: dependency reference {r.dep}@{r.version} "
                    f"is ambiguous — declared ranges {', '.join(r.ranges)} (E4b)"
                )
        return problems

    def run(self, module: Module) -> DepResolution:
        problems = self.validate(module)
        if problems:
            raise _dep_error(
                "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else "")
            )
        return self.resolve(module)
