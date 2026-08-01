// W3 session/OTP - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} session_valid(token: int) returns (result: Result<(), string>)
  requires token >= 0
  ensures result.Ok? == (token >= 1000)

method {:extern}{:axiom} otp_check(phone: int, code: int) returns (result: Result<(), string>)
  requires phone >= 0
  requires code >= 0
  ensures result.Ok? == (code == phone + 1000)

method login(token: int, phone: int, code: int) returns (result: Result<(), string>)
  requires token >= 0
  requires phone >= 0
  requires code >= 0
  ensures result.Ok? == (token >= 1000 && code == phone + 1000)
{
  var s := session_valid(token);
  if s.Ok? != (token >= 1000) {
    result := Err("contract violation");
    return;
  }
  if !s.Ok? {
    result := Err(s.error);
    return;
  }
  var o := otp_check(phone, code);
  if o.Ok? != (code == phone + 1000) {
    result := Err("contract violation");
    return;
  }
  if !o.Ok? {
    result := Err(o.error);
    return;
  }
  result := Ok(());
}
