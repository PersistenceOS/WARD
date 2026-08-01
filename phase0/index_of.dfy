datatype List<T> = Nil | Cons(head: T, tail: List<T>)

function len(xs: List<int>): nat
  decreases xs
{
  match xs
  case Nil => 0
  case Cons(_, t) => 1 + len(t)
}

function get(xs: List<int>, i: nat): int
  requires i < len(xs)
  decreases xs
{
  match xs
  case Nil => 0
  case Cons(h, t) => if i == 0 then h else get(t, i - 1)
}

function contains(xs: List<int>, v: int): bool
  decreases xs
{
  match xs
  case Nil => false
  case Cons(h, t) => h == v || contains(t, v)
}

method index_of(xs: List<int>, v: int) returns (result: int)
  ensures result == -1 || result >= 0
  ensures result == -1 ==> !contains(xs, v)
  ensures result >= 0 ==> result < len(xs) && get(xs, result) == v && forall j :: 0 <= j < result ==> get(xs, j) != v
  decreases xs
{
  match xs
  case Nil =>
    result := -1;
  case Cons(h, t) =>
    if h == v {
      result := 0;
    } else {
      var r := index_of(t, v);
      if r == -1 {
        result := -1;
      } else {
        result := r + 1;
      }
    }
}
