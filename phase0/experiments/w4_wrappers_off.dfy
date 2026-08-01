datatype Result<T, E> = Ok(value: T) | Err(error: E)
method {:extern}{:axiom} stock_check(item: int, qty: int) returns (result: Result<int, string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (item < 1000)
  ensures (!(result.Ok?)) || result.value == qty
method {:extern}{:axiom} inventory_reserve(item: int, qty: int) returns (result: Result<(), string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (qty <= 20)
method place_order(item: int, qty: int) returns (result: Result<int, string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (item < 1000 && qty <= 20)
{
  var w0 := stock_check(item, qty);
  var s := w0;
  if s.Ok? != (item < 1000) {
    return Err("contract violation");
  }
  if s.Err? {
    return Err(s.error);
  }
  var w1 := inventory_reserve(item, qty);
  var r := w1;
  if r.Ok? != (qty <= 20) {
    return Err("contract violation");
  }
  if r.Err? {
    return Err(r.error);
  }
  return Ok(s.value);
}
