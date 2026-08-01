predicate IsPow2(x: int)
  requires x >= 1
  decreases x
{
  x == 1 || (x % 2 == 0 && IsPow2(x / 2))
}

method is_power_of_two(n: int) returns (result: bool)
  requires 1 <= n <= 1000000000
  ensures result == IsPow2(n)
{
  var m := n;
  while m % 2 == 0 && m > 1
    invariant 1 <= m
    invariant IsPow2(n) == IsPow2(m)
    decreases m
  {
    m := m / 2;
  }
  result := m == 1;
}
