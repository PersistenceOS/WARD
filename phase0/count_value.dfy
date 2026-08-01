function count(xs: seq<int>, v: int): int {
    if |xs| == 0 then 0 else (if xs[|xs|-1] == v then 1 else 0) + count(xs[..|xs|-1], v)
}

lemma slice_append(xs: seq<int>, i: int)
    requires 0 <= i < |xs|
    ensures xs[0..i+1] == xs[0..i] + [xs[i]]
{
}

lemma count_append(xs: seq<int>, x: int, v: int)
    ensures count(xs + [x], v) == count(xs, v) + if x == v then 1 else 0
    decreases |xs|
{
}

method count_value(xs: seq<int>, v: int) returns (result: int)
    ensures result == count(xs, v)
{
    result := 0;
    var i := 0;
    while i < |xs|
        invariant 0 <= i <= |xs|
        invariant result == count(xs[0..i], v)
        decreases |xs| - i
    {
        slice_append(xs, i);
        count_append(xs[0..i], xs[i], v);
        if xs[i] == v {
            result := result + 1;
        }
        i := i + 1;
    }
    assert xs[0..i] == xs;
}
