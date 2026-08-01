method min3(a: int, b: int, c: int) returns (result: int)
    ensures result <= a
    ensures result <= b
    ensures result <= c
    ensures result == a || result == b || result == c
{
    if a <= b && a <= c {
        result := a;
    } else if b <= a && b <= c {
        result := b;
    } else {
        result := c;
    }
}
