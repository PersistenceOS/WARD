import sys
from typing import Callable, Any, TypeVar, NamedTuple
from math import floor
from itertools import count

import module_ as module_
import _dafny as _dafny
import System_ as System_

# Module: module_

class default__:
    def  __init__(self):
        pass

    @staticmethod
    def stripe__charge__checked(amount, token):
        result: Result = Result.default(_dafny.defaults.tuple())()
        d_0_r_: Result
        out0_: Result
        out0_ = default__.stripe__charge(amount, token)
        d_0_r_ = out0_
        if not(((d_0_r_).is_Ok) == ((amount) <= (100))):
            result = Result_Err(_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "contract violation")))
        elif True:
            result = d_0_r_
        return result

    @staticmethod
    def Main(noArgsParameter__):
        d_0_a_: Result
        out0_: Result
        out0_ = default__.stripe__charge__checked(50, _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "tok")))
        d_0_a_ = out0_
        d_1_b_: Result
        out1_: Result
        out1_ = default__.stripe__charge__checked(110, _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "tok")))
        d_1_b_ = out1_
        d_2_c_: Result
        out2_: Result
        out2_ = default__.stripe__charge__checked(150, _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "tok")))
        d_2_c_ = out2_
        _dafny.print((_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "a="))).VerbatimString(False))
        _dafny.print(_dafny.string_of(d_0_a_))
        _dafny.print((_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, " b="))).VerbatimString(False))
        _dafny.print(_dafny.string_of(d_1_b_))
        _dafny.print((_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, " c="))).VerbatimString(False))
        _dafny.print(_dafny.string_of(d_2_c_))
        _dafny.print((_dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, "\n"))).VerbatimString(False))


class Result:
    @classmethod
    def default(cls, default_T):
        return lambda: Result_Ok(default_T())
    def __ne__(self, __o: object) -> bool:
        return not self.__eq__(__o)
    @property
    def is_Ok(self) -> bool:
        return isinstance(self, Result_Ok)
    @property
    def is_Err(self) -> bool:
        return isinstance(self, Result_Err)

class Result_Ok(Result, NamedTuple('Ok', [('value', Any)])):
    def __dafnystr__(self) -> str:
        return f'Result.Ok({_dafny.string_of(self.value)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Result_Ok) and self.value == __o.value
    def __hash__(self) -> int:
        return super().__hash__()

class Result_Err(Result, NamedTuple('Err', [('error', Any)])):
    def __dafnystr__(self) -> str:
        return f'Result.Err({_dafny.string_of(self.error)})'
    def __eq__(self, __o: object) -> bool:
        return isinstance(__o, Result_Err) and self.error == __o.error
    def __hash__(self) -> int:
        return super().__hash__()


# --- injected stub (mirrors harness _stub_injection) ---
def stripe_charge_stub(amount, token):
    # BUGGY: grants up to 120, contract says Ok only up to 100 -> over-grant 101..120
    if amount <= 120:
        return ("ok", None)
    return ("err", "declined")

def _dafny_str(s):
    return _dafny.SeqWithoutIsStrInference(map(_dafny.CodePoint, s))

def _adapt_stripe_charge(amount, token):
    kind, val = stripe_charge_stub(amount, token)
    if kind == "ok":
        return Result_Ok(())
    return Result_Err(_dafny_str(val))

default__.stripe__charge = staticmethod(_adapt_stripe_charge)
