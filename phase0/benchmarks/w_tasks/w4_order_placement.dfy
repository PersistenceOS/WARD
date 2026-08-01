// W4 order placement - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} stock_check(item: int, qty: int) returns (result: Result<int, string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (item < 1000)
  ensures !result.Ok? || result.value == qty

method {:extern}{:axiom} inventory_reserve(item: int, qty: int) returns (result: Result<(), string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (qty <= 20)

method place_order(item: int, qty: int) returns (result: Result<int, string>)
  requires item > 0
  requires qty > 0
  ensures result.Ok? == (item < 1000 && qty <= 20)
{
  var s := stock_check(item, qty);
  if s.Ok? != (item < 1000) {
    result := Err("contract violation");
    return;
  }
  if !s.Ok? {
    result := Err(s.error);
    return;
  }
  var r := inventory_reserve(item, qty);
  if r.Ok? != (qty <= 20) {
    result := Err("contract violation");
    return;
  }
  if !r.Ok? {
    result := Err(r.error);
    return;
  }
  result := Ok(s.value);
}
