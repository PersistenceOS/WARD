// W1 payment chain - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} auth_check(user_id: int) returns (result: Result<(), string>)
  requires user_id > 0
  ensures result.Ok? == (user_id < 1000)

method {:extern}{:axiom} rate_limit(amount: int) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 5000)

method {:extern}{:axiom} stripe_charge(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)

method pay(user_id: int, amount: int, token: string) returns (result: Result<(), string>)
  requires user_id > 0
  requires amount > 0
  ensures result.Ok? == (user_id < 1000 && amount <= 100)
{
  var a := auth_check(user_id);
  if a.Ok? != (user_id < 1000) {
    result := Err("contract violation");
    return;
  }
  if !a.Ok? {
    result := Err(a.error);
    return;
  }
  var r := rate_limit(amount);
  if r.Ok? != (amount <= 5000) {
    result := Err("contract violation");
    return;
  }
  if !r.Ok? {
    result := Err(r.error);
    return;
  }
  var c := stripe_charge(amount, token);
  if c.Ok? != (amount <= 100) {
    result := Err("contract violation");
    return;
  }
  if c.Ok? {
    result := Ok(());
  } else {
    result := Err(c.error);
  }
}
