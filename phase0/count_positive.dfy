datatype List<T> = Nil | Cons(head: T, tail: List<T>)

function length<T>(xs: List<T>): nat {
  match xs
  case Nil => 0
  case Cons(_, t) => 1 + length(t)
}

method count_positive(xs: List<int>) returns (result: int)
  ensures 0 <= result <= length(xs)
  decreases xs
{
  match xs
  case Nil =>
    result := 0;
  case Cons(h, t) =>
    var r := count_positive(t);
    result := r + if h > 0 then 1 else 0;
}
