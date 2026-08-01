function Fact(n: int): int
    requires 0 <= n <= 12
{
    if n == 0 then 1 else n * Fact(n - 1)
}

method factorial(n: int) returns (result: int)
    requires 0 <= n <= 12
    ensures result == Fact(n)
{
    result := 1;
    var i := 1;
    while i <= n
        invariant 1 <= i <= n + 1
        invariant result == Fact(i - 1)
        decreases n - i
    {
        result := result * i;
        i := i + 1;
    }
}
