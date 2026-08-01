method is_prime(n: int) returns (result: bool)
  requires n >= 2
  ensures result == (forall k :: 2 <= k < n ==> n % k != 0)
{
  var k := 2;
  result := true;
  while k < n
    invariant 2 <= k <= n
    invariant result == (forall j :: 2 <= j < k ==> n % j != 0)
    decreases n - k
  {
    if n % k == 0 {
      result := false;
    }
    k := k + 1;
  }
}
