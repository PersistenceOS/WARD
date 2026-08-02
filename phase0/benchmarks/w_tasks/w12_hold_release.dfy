// W12 hold + release - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} escrow_hold(account: int, amount: int) returns (result: Result<int, string>)
  requires account > 0
  requires amount > 0
  ensures result.Ok? == (amount <= 500)
  ensures !result.Ok? || result.value == account

method {:extern}{:axiom} escrow_release(hold_id: int) returns (result: Result<int, string>)
  requires hold_id > 0
  ensures result.Ok? == (hold_id < 1000)
  ensures !result.Ok? || result.value == hold_id

method hold_and_release(account: int, amount: int, hold_id: int) returns (result: Result<int, string>)
  requires account > 0
  requires amount > 0
  requires hold_id > 0
  ensures result.Ok? == (amount <= 500 && hold_id < 1000)
{
  var h := hold_funds(account, amount);
  if !h.Ok? {
    result := Err(h.error);
    return;
  }
  var r := release_hold(hold_id);
  if !r.Ok? {
    result := Err(r.error);
    return;
  }
  result := Ok(r.value);
}

method hold_funds(account: int, amount: int) returns (result: Result<int, string>)
  requires account > 0
  requires amount > 0
  ensures result.Ok? == (amount <= 500)
  ensures !result.Ok? || result.value == account
{
  var h := escrow_hold(account, amount);
  if h.Ok? != (amount <= 500) {
    result := Err("contract violation");
    return;
  }
  if !h.Ok? {
    result := Err(h.error);
    return;
  }
  result := Ok(h.value);
}

method release_hold(hold_id: int) returns (result: Result<int, string>)
  requires hold_id > 0
  ensures result.Ok? == (hold_id < 1000)
  ensures !result.Ok? || result.value == hold_id
{
  var r := escrow_release(hold_id);
  if r.Ok? != (hold_id < 1000) {
    result := Err("contract violation");
    return;
  }
  if !r.Ok? {
    result := Err(r.error);
    return;
  }
  result := Ok(r.value);
}
