datatype Result<T, E> = Ok(value: T) | Err(error: E)

method withdraw_twice(balance: int, first: int, second: int) returns (result: Result<int, string>)
    requires balance >= 0 && first > 0 && second > 0
    ensures match result {
        case Ok(v) => v == balance - first - second
        case Err(e) => e == "insufficient funds"
    }
{
    if first > balance {
        result := Err("insufficient funds");
    } else if second > balance - first {
        result := Err("insufficient funds");
    } else {
        result := Ok(balance - first - second);
    }
}
