method has_sum_pair(xs: seq<int>, target: int) returns (result: bool)
  ensures result <==> exists i, j ::
    0 <= i < j < |xs| && xs[i] + xs[j] == target
{
  result := false;
  var n := |xs|;
  var i := 0;
  while i < n
    invariant 0 <= i <= n
    invariant result <==> exists ii, jj ::
      0 <= ii < i && ii < jj < n && xs[ii] + xs[jj] == target
  {
    var j := i + 1;
    while j < n
      invariant i < j <= n
      invariant result <==> exists ii, jj ::
        (0 <= ii < i && ii < jj < n && xs[ii] + xs[jj] == target)
        || (ii == i && i < jj < j && xs[ii] + xs[jj] == target)
    {
      if xs[i] + xs[j] == target {
        result := true;
      }
      j := j + 1;
    }
    i := i + 1;
  }
}
