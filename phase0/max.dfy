datatype List<T> = Nil | Cons(head: T, tail: List<T>)

ghost function MaxOfList(xs: List<int>): int
  requires xs != Nil
{
  match xs {
    case Nil => 0
    case Cons(h, t) =>
      if t == Nil then h else if h >= MaxOfList(t) then h else MaxOfList(t)
  }
}

method max_of_list(xs: List<int>) returns (result: int)
  requires xs != Nil
  ensures result == MaxOfList(xs)
  decreases xs
{
  var Cons(h, t) := xs;
  if t == Nil {
    result := h;
  } else {
    var m := max_of_list(t);
    result := if h >= m then h else m;
  }
}
