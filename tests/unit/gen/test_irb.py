"""``irb`` value helpers, rendered through be-sv (gaps G4 and G7)."""
import zuspec.ir.core as ir
from zuspec.be.sv.ir.expr_emit import SVExprEmitter

from fw.hdl.gen import irb


def _sv(x):
    return SVExprEmitter().emit(x)


def test_zero_by_type():
    assert _sv(irb.zero(ir.DataTypeInt(bits=8, signed=False))) == "0"
    assert _sv(irb.zero(ir.DataTypeClass(super=None))) == "null"
    assert _sv(irb.zero(ir.DataTypeStruct(super=None))) == "'{default: 0}"


def test_ne_case_inequality():
    a, b = irb.ref("a"), irb.ref("b")
    assert _sv(irb.ne(a, b, ir.DataTypeInt(bits=4, signed=False))) == "(a !== b)"
    assert _sv(irb.ne(a, b)) == "(a !== b)"
    assert _sv(irb.ne(a, b, ir.DataTypeString())) == "(a != b)"
