function Sum(lo: int, hi: int): int
  decreases hi - lo
{
  if lo > hi then 0 else Sum(lo, hi - 1) + hi
}

method sum_range(lo: int, hi: int) returns (result: int)
  requires 0 <= lo <= hi
  ensures result == Sum(lo, hi)
{
  var i := lo - 1;
  result := 0;
  while i < hi
    invariant lo - 1 <= i <= hi
    invariant result == Sum(lo, i)
    decreases hi - i
  {
    i := i + 1;
    result := result + i;
  }
}
