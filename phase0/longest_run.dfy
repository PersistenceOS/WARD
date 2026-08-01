method longest_run(xs: seq<int>, v: int) returns (result: int)
  requires |xs| > 0
  ensures forall i, j :: 0 <= i <= j < |xs| && (forall k :: i <= k <= j ==> xs[k] == v) ==> j - i + 1 <= result
  ensures result == 0 || (exists i, j :: 0 <= i <= j < |xs| && (forall k :: i <= k <= j ==> xs[k] == v) && j - i + 1 == result)
{
  var best := 0;
  var i := 0;
  while i < |xs|
    decreases |xs| - i
    invariant 0 <= i <= |xs|
    invariant i == 0 || i == |xs| || xs[i-1] != v || xs[i] != v
    invariant forall i', j' :: 0 <= i' <= j' < i && (forall k :: i' <= k <= j' ==> xs[k] == v) ==> j' - i' + 1 <= best
    invariant best == 0 || (exists i', j' :: 0 <= i' <= j' < i && (forall k :: i' <= k <= j' ==> xs[k] == v) && j' - i' + 1 == best)
  {
    if xs[i] == v {
      var j := i;
      while j < |xs| && xs[j] == v
        decreases |xs| - j
        invariant i <= j <= |xs|
        invariant forall k :: i <= k < j ==> xs[k] == v
      {
        j := j + 1;
      }
      var L := j - i;
      if L > best {
        assert 1 <= L;
        assert i <= j - 1;
        assert forall k :: i <= k <= j - 1 ==> xs[k] == v;
        best := L;
      }
      i := j;
    } else {
      i := i + 1;
    }
  }
  result := best;
}
