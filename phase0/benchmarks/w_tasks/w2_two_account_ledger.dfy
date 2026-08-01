// W2 two-account ledger - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} ledger_debit(balance: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok? == (amount <= balance)
  ensures !result.Ok? || result.value == balance - amount

method {:extern}{:axiom} ledger_credit(balance: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok?
  ensures result.value == balance + amount

method transfer(balance: int, credit_bal: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires credit_bal >= 0
  requires amount >= 0
  ensures result.Ok? == (amount <= balance)
{
  var d := ledger_debit(balance, amount);
  if d.Ok? != (amount <= balance) {
    result := Err("contract violation");
    return;
  }
  if !d.Ok? {
    result := Err(d.error);
    return;
  }
  var c := ledger_credit(credit_bal, amount);
  if !c.Ok? {
    result := Err("contract violation");
    return;
  }
  result := Ok(d.value);
}
