method abs(x: int) returns (result: int)
    ensures result == (if x >= 0 then x else -x)
{
    if x >= 0 {
        result := x;
    } else {
        result := -x;
    }
}
