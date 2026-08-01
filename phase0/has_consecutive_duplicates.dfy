method has_consecutive_duplicates(xs: seq<int>) returns (result: bool)
  ensures result == exists i :: 0 <= i < |xs| - 1 && xs[i] == xs[i + 1]
{
  if |xs| < 2 {
    result := false;
  } else {
    var i := 0;
    result := false;
    while i < |xs| - 1
      invariant 0 <= i <= |xs| - 1
      invariant result == (exists j :: 0 <= j < i && xs[j] == xs[j + 1])
      decreases |xs| - 1 - i
    {
      if xs[i] == xs[i + 1] {
        result := true;
      }
      i := i + 1;
    }
  }
}
