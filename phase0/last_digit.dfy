method last_digit(n: int) returns (result: int)
    requires n >= 0
    ensures result == n % 10
{
    result := n % 10;
}
