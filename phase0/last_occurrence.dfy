method last_occurrence(xs: seq<int>, v: int) returns (result: int)
    requires true
    ensures result == -1 <==> !(v in xs)
    ensures -1 <= result < |xs|
    ensures result != -1 ==> xs[result] == v
    ensures result != -1 ==> forall i :: result < i < |xs| ==> xs[i] != v
    ensures result == -1 ==> forall i :: 0 <= i < |xs| ==> xs[i] != v
{
    result := -1;
    var i := 0;
    while i < |xs|
        invariant 0 <= i <= |xs|
        invariant result == -1 ==> forall k :: 0 <= k < i ==> xs[k] != v
        invariant result != -1 ==> 0 <= result < i && xs[result] == v
        invariant result != -1 ==> forall k :: result < k < i ==> xs[k] != v
        decreases |xs| - i
    {
        if xs[i] == v {
            result := i;
        }
        i := i + 1;
    }
}
