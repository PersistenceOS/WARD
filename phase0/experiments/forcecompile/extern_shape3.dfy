datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} stripe_charge(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)

method stripe_charge_checked(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)
{
  var r := stripe_charge(amount, token);
  if !(r.Ok? == (amount <= 100)) {
    result := Err("contract violation");
  } else {
    result := r;
  }
}

method Main() {
  var a := stripe_charge_checked(50, "tok");
  var b := stripe_charge_checked(110, "tok");   // OVER-GRANT region: stub grants Ok, contract says Err
  var c := stripe_charge_checked(150, "tok");   // both agree: Err
  print "a=", a, " b=", b, " c=", c, "\n";
}
