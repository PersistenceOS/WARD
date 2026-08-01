method sum_between(xs: seq<int>, lo: int, hi: int) returns (result: int)
    requires 0 <= lo <= hi <= |xs|
    requires forall i :: 0 <= i < |xs| ==> xs[i] >= 0
    ensures result == SumBetween(xs, lo, hi)
    ensures result >= 0
{
    var i := lo;
    var s := 0;
    while i < hi
        invariant lo <= i <= hi
        invariant s == SumBetween(xs, lo, i)
        invariant s >= 0
        decreases hi - i
    {
        s := s + xs[i];
        i := i + 1;
    }
    result := s;
}

function SumBetween(xs: seq<int>, lo: int, hi: int): int
    requires 0 <= lo <= hi <= |xs|
{
    if lo == hi then 0 else SumBetween(xs, lo, hi - 1) + xs[hi - 1]
}
