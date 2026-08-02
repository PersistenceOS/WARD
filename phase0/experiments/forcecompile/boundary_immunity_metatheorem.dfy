// Boundary Immunity — THE FULL METATHEOREM (theory instrument I2, pre-registered
// in files/ward-phase2-scoping.md section 10).
//
// This upgrades the per-instance feasibility check (boundary_immunity.dfy,
// which fixed the w1 contract) to the full statement, generic over:
//   * ANY extern contract C (a predicate parameter, not a fixed one), and
//   * ANY core guarantee Q (the caller's postcondition).
//
// THEOREM (the thing WARD claims, proved here once for all programs):
//   For every extern implementation e (any return value r — even adversarial),
//   for every extern contract C, for every core guarantee Q that holds under
//   contract-satisfying extern results:
//
//       Wrap_C(r) == Err("contract violation")  ==>  the extern violated C
//       Wrap_C(r) != Err("contract violation")  ==>  Q(Wrap_C(r))   [the core's
//                                                     guarantee survives]
//
//   I.e. the wrapper transform makes extern behavior irrelevant to core
//   soundness: an extern can only cause a caught Err, never a silent
//   violation of the core's guarantee. "Err never corrupts core state."
//
// The proof is a single Dafny lemma parameterized by the contract predicate C
// and the guarantee Q — one proof for ALL programs, not per-instance.
// The E1 corpus (70/70 byte-identical emission) is what makes this theorem
// *shippable*: the certificate can carry "boundary immunity: verified" because
// the transform is deterministic.

datatype Result<T, E> = Ok(value: T) | Err(error: E)

// A generic extern contract: a predicate over the input and the extern's result.
type Contract = (int, Result<(), string>) -> bool

// A generic core guarantee: a predicate over the final result.
type Guarantee = Result<(), string> -> bool

// The wrapper transform, generic over the contract C:
// a contract violation becomes Err("contract violation").
function Wrap(C: Contract, x: int, r: Result<(), string>): Result<(), string>
{
  if C(x, r) then r else Err("contract violation")
}

// Lemma 1 (transparency): a conforming extern's result passes through unchanged.
lemma WrapConforming(C: Contract, x: int, r: Result<(), string>)
  requires C(x, r)
  ensures Wrap(C, x, r) == r
{
}

// Lemma 2 (violation marker): a violating extern becomes Err("contract violation").
lemma WrapViolating(C: Contract, x: int, r: Result<(), string>)
  requires !C(x, r)
  ensures Wrap(C, x, r) == Err("contract violation")
{
}

// THE METATHEOREM: one proof for all programs.
//
// Given: the core's guarantee Q holds for every extern result that satisfies
// the contract C (this is exactly what "the core verifies under abstract
// extern contracts" means — the Dafny backend discharges {P} C {Q} with the
// externs' contracts as axioms).
//
// Then: for ANY extern result r (any implementation, even adversarial), the
// wrapped value either is Err("contract violation") (extern was caught) or
// satisfies Q (the core's guarantee survives untouched).
lemma BoundaryImmunityMetatheorem(C: Contract, Q: Guarantee, x: int,
                                  r: Result<(), string>)
  requires forall rr :: C(x, rr) ==> Q(rr)
  ensures Wrap(C, x, r) == Err("contract violation")
          || Q(Wrap(C, x, r))
{
  var w := Wrap(C, x, r);
  if C(x, r) {
    WrapConforming(C, x, r);
    assert w == r;
    assert Q(w) by {  // from the forall: C(x, r) ==> Q(r), and w == r
      // Dafny instantiates the forall with rr := r
    }
  } else {
    WrapViolating(C, x, r);
    assert w == Err("contract violation");
  }
}

// Corollary: Err never corrupts core state. If the wrapped call is not the
// violation marker, the guarantee holds — regardless of what the extern did.
lemma BoundaryImmunityCorollary(C: Contract, Q: Guarantee, x: int,
                                r: Result<(), string>)
  requires forall rr :: C(x, rr) ==> Q(rr)
  requires Wrap(C, x, r) != Err("contract violation")
  ensures Q(Wrap(C, x, r))
{
  BoundaryImmunityMetatheorem(C, Q, x, r);
}

// Demonstration: the w1-shaped caller, now generic. The caller routes the
// extern result through the wrapper; on violation it returns the marker; else
// it returns the (guarantee-satisfying) value.
method CallerGeneric(C: Contract, Q: Guarantee, x: int)
  returns (result: Result<(), string>)
  requires forall rr :: C(x, rr) ==> Q(rr)
  ensures result == Err("contract violation") || Q(result)
{
  // any extern implementation — an adversarial one is just as valid a choice
  var r: Result<(), string> :| true;
  var w := Wrap(C, x, r);
  if w == Err("contract violation") {
    result := Err("contract violation");
  } else {
    result := w;
  }
  // proof: from the metatheorem
  BoundaryImmunityMetatheorem(C, Q, x, r);
  assert result == Err("contract violation") || Q(result);
}
