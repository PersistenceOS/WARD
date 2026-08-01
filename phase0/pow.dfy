function Pow(base: int, e: nat): int
{
  if e == 0 then 1 else base * Pow(base, e - 1)
}

method pow(base: int, e: int) returns (result: int)
  requires base >= 0
  requires 0 <= e <= 20
  ensures result == Pow(base, e)
{
  result := 1;
  var i: nat := 0;
  while i < e
    decreases e - i
    invariant i <= e
    invariant result == Pow(base, i)
  {
    result := result * base;
    i := i + 1;
  }
}
