method is_ascending(xs: seq<int>) returns (result: bool)
  requires |xs| >= 2
  ensures result == (forall i :: 1 <= i < |xs| ==> xs[i-1] < xs[i])
{
  var i := 1;
  result := true;
  while i < |xs|
    invariant 1 <= i <= |xs|
    invariant result == (forall j :: 1 <= j < i ==> xs[j-1] < xs[j])
    decreases |xs| - i
  {
    if xs[i-1] >= xs[i] {
      result := false;
    }
    i := i + 1;
  }
}
