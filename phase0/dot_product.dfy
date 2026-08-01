function DotProductSum(xs: seq<int>, ys: seq<int>): int
  requires |xs| == |ys|
  decreases |xs|
{
  if |xs| == 0 then 0
  else xs[0] * ys[0] + DotProductSum(xs[1..], ys[1..])
}

method dot_product(xs: seq<int>, ys: seq<int>) returns (result: int)
  requires |xs| == |ys|
  requires forall i :: 0 <= i < |xs| ==> 0 <= xs[i] && 0 <= ys[i]
  ensures result == DotProductSum(xs, ys)
{
  var acc := 0;
  var i := 0;
  while i < |xs|
    decreases |xs| - i
    invariant 0 <= i <= |xs|
    invariant acc + DotProductSum(xs[i..], ys[i..]) == DotProductSum(xs, ys)
  {
    acc := acc + xs[i] * ys[i];
    i := i + 1;
  }
  result := acc;
}
