// W11 idempotent retry - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} dedup_lookup(tx_id: int) returns (result: Result<(), string>)
  requires tx_id > 0
  ensures result.Ok? == (tx_id < 1000)

method {:extern}{:axiom} gateway_charge(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)

method retry_payment(tx_id: int, amount: int, token: string) returns (result: Result<(), string>)
  requires tx_id > 0
  requires amount > 0
  ensures result.Ok? == (tx_id < 1000 && amount <= 100)
{
  var d := dedup_check(tx_id);
  if !d.Ok? {
    result := Err(d.error);
    return;
  }
  var c := charge_once(amount, token);
  if !c.Ok? {
    result := Err(c.error);
    return;
  }
  result := Ok(());
}

method dedup_check(tx_id: int) returns (result: Result<(), string>)
  requires tx_id > 0
  ensures result.Ok? == (tx_id < 1000)
{
  var d := dedup_lookup(tx_id);
  if d.Ok? != (tx_id < 1000) {
    result := Err("contract violation");
    return;
  }
  if !d.Ok? {
    result := Err(d.error);
    return;
  }
  result := Ok(());
}

method charge_once(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)
{
  var c := gateway_charge(amount, token);
  if c.Ok? != (amount <= 100) {
    result := Err("contract violation");
    return;
  }
  if !c.Ok? {
    result := Err(c.error);
    return;
  }
  result := Ok(());
}
