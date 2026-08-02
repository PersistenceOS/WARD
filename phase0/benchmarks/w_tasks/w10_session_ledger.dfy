// W10 session + ledger - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} session_validate(user_id: int, otp: int) returns (result: Result<(), string>)
  requires user_id > 0
  requires otp >= 0
  ensures result.Ok? == (user_id < 1000 && otp == 123456)

method {:extern}{:axiom} ledger_debit(balance: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok? == (amount <= balance)
  ensures !result.Ok? || result.value == balance - amount

method session_transfer(user_id: int, otp: int, balance: int, amount: int) returns (result: Result<int, string>)
  requires user_id > 0
  requires otp >= 0
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok? == (user_id < 1000 && otp == 123456 && amount <= balance)
{
  var a := auth_session(user_id, otp);
  if !a.Ok? {
    result := Err(a.error);
    return;
  }
  var d := ledger_move(balance, amount);
  if !d.Ok? {
    result := Err(d.error);
    return;
  }
  result := Ok(d.value);
}

method auth_session(user_id: int, otp: int) returns (result: Result<(), string>)
  requires user_id > 0
  requires otp >= 0
  ensures result.Ok? == (user_id < 1000 && otp == 123456)
{
  var a := session_validate(user_id, otp);
  if a.Ok? != (user_id < 1000 && otp == 123456) {
    result := Err("contract violation");
    return;
  }
  if !a.Ok? {
    result := Err(a.error);
    return;
  }
  result := Ok(());
}

method ledger_move(balance: int, amount: int) returns (result: Result<int, string>)
  requires balance >= 0
  requires amount >= 0
  ensures result.Ok? == (amount <= balance)
  ensures !result.Ok? || result.value == balance - amount
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
  result := Ok(d.value);
}
