datatype List<T> = Nil | Cons(head: T, tail: List<T>)

predicate In(x: int, xs: List<int>) {
  match xs
  case Nil => false
  case Cons(h, t) => h == x || In(x, t)
}

predicate AllAtMost(x: int, xs: List<int>) {
  match xs
  case Nil => true
  case Cons(h, t) => h <= x && AllAtMost(x, t)
}

lemma AllAtMostMonotone(x: int, y: int, xs: List<int>)
  requires AllAtMost(x, xs)
  requires x <= y
  ensures AllAtMost(y, xs)
  decreases xs
{
  match xs
  case Nil =>
  case Cons(h, t) =>
    AllAtMostMonotone(x, y, t);
}

method max_of_list(xs: List<int>) returns (result: int)
  requires xs != Nil
  ensures In(result, xs)
  ensures AllAtMost(result, xs)
  decreases xs
{
  var Cons(h, t) := xs;
  if t == Nil {
    result := h;
  } else {
    var m := max_of_list(t);
    if m >= h {
      result := m;
    } else {
      AllAtMostMonotone(m, h, t);
      result := h;
    }
  }
}
