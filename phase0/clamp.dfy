method clamp(x: int, lo: int, hi: int) returns (result: int)
    requires lo <= hi
    ensures if x < lo then result == lo else if x > hi then result == hi else result == x
{
    if x < lo {
        result := lo;
    } else if x > hi {
        result := hi;
    } else {
        result := x;
    }
}
