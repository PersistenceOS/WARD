// W6 CRUD handler - raw Dafny reference (D arm)
datatype Result<T, E> = Ok(value: T) | Err(error: E)

method {:extern}{:axiom} db_get(key: int) returns (result: Result<int, string>)
  requires key > 0
  ensures result.Ok? == (key < 1000)

method {:extern}{:axiom} db_put(key: int, value: int) returns (result: Result<(), string>)
  requires key > 0
  ensures result.Ok? == (key < 1000)

method crud_op(op: int, key: int, value: int) returns (result: Result<int, string>)
  requires op >= 1
  requires key > 0
  ensures result.Ok? == ((op == 1 || op == 2) && key < 1000)
{
  if op == 1 {
    var v := db_get(key);
    if v.Ok? != (key < 1000) {
      result := Err("contract violation");
      return;
    }
    if !v.Ok? {
      result := Err(v.error);
      return;
    }
    result := Ok(v.value);
    return;
  }
  if op == 2 {
    var u := db_put(key, value);
    if u.Ok? != (key < 1000) {
      result := Err("contract violation");
      return;
    }
    if !u.Ok? {
      result := Err(u.error);
      return;
    }
    result := Ok(0);
    return;
  }
  result := Err("bad op");
}
