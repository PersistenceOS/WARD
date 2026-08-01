// W7 idempotency - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} dedup_lookup(key: int) returns (result: Result<int, string>)
  requires key > 0
  ensures result.Ok?
  ensures (key < 500 && result.value == key + 1) || (key >= 500 && result.value == 0)

method {:extern}{:axiom} gateway_charge(key: int, amount: int) returns (result: Result<(), string>)
  requires key > 0
  requires amount > 0
  ensures result.Ok? == (amount <= 100)

method charge_idempotent(key: int, amount: int) returns (result: Result<int, string>)
  requires key > 0
  requires amount > 0
  ensures result.Ok? == (key < 500 || amount <= 100)
{
  var d := dedup_lookup(key);
  if !d.Ok? {
    result := Err("contract violation");
    return;
  }
  if (d.value == 0) != (key >= 500) {
    result := Err("contract violation");
    return;
  }
  if d.value != 0 {
    result := Ok(d.value);
    return;
  }
  var c := gateway_charge(key, amount);
  if c.Ok? != (amount <= 100) {
    result := Err("contract violation");
    return;
  }
  if !c.Ok? {
    result := Err(c.error);
    return;
  }
  result := Ok(0);
}
