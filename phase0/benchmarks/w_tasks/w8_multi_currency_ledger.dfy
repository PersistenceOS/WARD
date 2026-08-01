// W8 multi-currency ledger - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} fx_rate(pair: int) returns (result: Result<int, string>)
  requires pair >= 1
  ensures result.Ok? == (pair <= 3)
  ensures !result.Ok? || result.value == pair * 10

method {:extern}{:axiom} ledger_debit(balance: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok? == (amount <= balance)
  ensures !result.Ok? || result.value == balance - amount

method convert_transfer(balance: int, amount: int, pair: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  requires pair >= 1
  ensures result.Ok? == (amount <= balance && pair <= 3)
{
  var r := fx_rate(pair);
  if r.Ok? != (pair <= 3) {
    result := Err("contract violation");
    return;
  }
  if !r.Ok? {
    result := Err(r.error);
    return;
  }
  var d := ledger_debit(balance, amount);
  if d.Ok? != (amount <= balance) {
    result := Err("contract violation");
    return;
  }
  if !d.Ok? {
    result := Err(d.error);
    return;
  }
  result := Ok(d.value * r.value);
}
