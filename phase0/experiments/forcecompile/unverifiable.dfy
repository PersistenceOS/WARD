// Repro for the exit-code claim in ward-phase1-experiment-design.md §6.2:
// WITHOUT --no-verify this exits 4 (verification blocks translation);
// WITH --no-verify it exits 0 and emits runnable Python.
method charge(amount: int) returns (result: int)
  requires amount > 0
  ensures result == amount * 2
{
  result := amount + 1;   // deliberately wrong: ensures unprovable
}

method Main() {
  var r := charge(50);
  print "charge=", r, "\n";
}
