method is_palindrome(xs: seq<int>) returns (result: bool)
    ensures result ==> forall i :: 0 <= i < |xs| / 2 ==> xs[i] == xs[|xs| - 1 - i]
    ensures !result ==> exists i :: 0 <= i < |xs| / 2 && xs[i] != xs[|xs| - 1 - i]
{
    var i := 0;
    var ok := true;
    while i < |xs| / 2
        invariant 0 <= i <= |xs| / 2
        invariant ok ==> forall j :: 0 <= j < i ==> xs[j] == xs[|xs| - 1 - j]
        invariant !ok ==> exists j :: 0 <= j < i && xs[j] != xs[|xs| - 1 - j]
    {
        if xs[i] != xs[|xs| - 1 - i] {
            ok := false;
        }
        i := i + 1;
    }
    result := ok;
}
