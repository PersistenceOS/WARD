function SumEven(xs: seq<int>): int
    decreases xs
{
    if |xs| == 0 then 0 else (if xs[0] % 2 == 0 then xs[0] else 0) + SumEven(xs[1..])
}

method sum_evens(xs: seq<int>) returns (result: int)
    requires forall i :: 0 <= i < |xs| ==> xs[i] >= 0
    ensures result == SumEven(xs)
    ensures result >= 0
    decreases xs
{
    if |xs| == 0 {
        result := 0;
    } else {
        var rest := sum_evens(xs[1..]);
        result := rest + (if xs[0] % 2 == 0 then xs[0] else 0);
    }
}
