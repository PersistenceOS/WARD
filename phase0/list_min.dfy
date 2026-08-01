datatype List<T> = Nil | Cons(head: T, tail: List<T>)

function Contains<T(==)>(xs: List<T>, x: T): bool
  decreases xs
{
  match xs
  case Nil => false
  case Cons(h, t) => h == x || Contains(t, x)
}

method list_min(xs: List<int>) returns (result: int)
  requires xs != Nil
  ensures Contains(xs, result)
  ensures forall x :: Contains(xs, x) ==> result <= x
  decreases xs
{
  match xs
  case Nil => assume {:axiom} false;
  case Cons(h, t) =>
    if t == Nil {
      result := h;
    } else {
      var mt := list_min(t);
      if h <= mt {
        result := h;
      } else {
        result := mt;
      }
    }
}
