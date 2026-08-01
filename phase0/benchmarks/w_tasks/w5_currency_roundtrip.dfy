// W5 currency round-trip - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} fx_convert(amount: int, pair: int) returns (result: Result<int, string>)
  requires amount >= 0
  requires pair >= 1
  ensures result.Ok? == (pair == 1 || (pair == 2 && amount <= 1000))
  ensures !result.Ok? || (pair == 1 && result.value == amount * 2) || (pair == 2 && result.value == amount / 2)

method round_trip(amount: int) returns (result: Result<int, string>)
  requires amount >= 0
  ensures result.Ok? == (amount <= 500)
{
  var a := fx_convert(amount, 1);
  if !a.Ok? {
    result := Err("contract violation");
    return;
  }
  var av := a.value;
  var b := fx_convert(av, 2);
  if b.Ok? != (av <= 1000) {
    result := Err("contract violation");
    return;
  }
  if !b.Ok? {
    result := Err(b.error);
    return;
  }
  result := Ok(b.value);
}
