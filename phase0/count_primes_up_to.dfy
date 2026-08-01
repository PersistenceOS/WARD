predicate isPrime(i: int)
    requires i >= 2
{
    forall d :: 2 <= d < i ==> i % d != 0
}

function countPrimes(lo: int, hi: int): int
    requires 2 <= lo <= hi + 1
    decreases hi - lo
{
    if hi < lo then 0 else countPrimes(lo, hi - 1) + (if isPrime(hi) then 1 else 0)
}

method count_primes_up_to(n: int) returns (result: int)
    requires 2 <= n <= 100
    ensures result == countPrimes(2, n)
{
    var count := 0;
    var i := 1;
    while i < n
        invariant 1 <= i <= n
        invariant count == countPrimes(2, i)
        decreases n - i
    {
        i := i + 1;
        var p := true;
        var j := 2;
        while j < i
            invariant 2 <= j <= i
            invariant p <==> forall d :: 2 <= d < j ==> i % d != 0
            decreases i - j
        {
            if i % j == 0 {
                p := false;
            }
            j := j + 1;
        }
        assert p == isPrime(i);
        if p {
            count := count + 1;
        }
    }
    result := count;
}
