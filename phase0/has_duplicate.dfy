method has_duplicate(xs: seq<int>) returns (result: bool)
  ensures result ==> exists i, j :: 0 <= i < j < |xs| && xs[i] == xs[j]
  ensures !result ==> forall i, j :: 0 <= i < j < |xs| ==> xs[i] != xs[j]
{
  result := false;
  ghost var gi := 0;
  ghost var gj := 0;
  var i := 0;
  while i < |xs| && !result
    invariant 0 <= i <= |xs|
    invariant result ==> 0 <= gi < gj < |xs| && xs[gi] == xs[gj]
    invariant !result ==> forall a, b :: 0 <= a < b < |xs| && a < i ==> xs[a] != xs[b]
    decreases |xs| - i
  {
    var j := i + 1;
    while j < |xs| && !result
      invariant 0 <= i < j <= |xs|
      invariant i < |xs|
      invariant result ==> 0 <= gi < gj < |xs| && xs[gi] == xs[gj]
      invariant !result ==> forall a, b :: 0 <= a < b < |xs| && a < i ==> xs[a] != xs[b]
      invariant !result ==> forall b :: i < b < j ==> xs[i] != xs[b]
      decreases |xs| - j
    {
      if xs[i] == xs[j] {
        result := true;
        gi := i;
        gj := j;
      }
      j := j + 1;
    }
    i := i + 1;
  }
}
