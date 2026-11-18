"""A proxy of a method with a return value keeps it in ``__ret`` and hands
it to ``<m>_post`` (the conformance scenarios have only tasks)."""
import shutil
import subprocess

import pytest
import zuspec.ir.core as ir

from fw.hdl.gen import irb, listener, proxy
from fw.hdl.gen.emit import to_sv
from fw.hdl.gen.kit import Kit, KitApi


def _kit():
    i32 = ir.DataTypeInt(bits=32, signed=True)
    proto = ir.DataTypeProtocol(name="p_if", methods=[
        irb.method("peek", [irb.arg("a", i32)], returns=i32)])
    api = KitApi("p_if", [], proto, ir.DataTypeRef(ref_name="p_if"))
    kit = Kit("p_pkg", [api])
    return [proto] + listener.decls(api, kit) + proxy.decls(api, kit)


def test_proxy_ret(tmp_path):
    sv = to_sv("p_pkg", _kit())
    assert "virtual function int peek(input int a);" in sv
    assert "int __ret;" in sv
    assert "__ret = m_target.peek(a);" in sv
    assert "m_listeners[i].peek_post(a, __ret);" in sv
    assert "return __ret;" in sv
    assert "pure virtual function void peek_post(input int a, input int __ret);" in sv

    if shutil.which("verilator") is None:
        pytest.skip("verilator not on PATH")
    f = tmp_path / "p_pkg.sv"
    f.write_text(sv + "module chk;\n  import p_pkg::*;\n"
                 "  initial begin p_if_proxy p; p = new; end\nendmodule\n")
    res = subprocess.run(["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME",
                          "-Wno-UNUSEDSIGNAL", str(f)],
                         capture_output=True, text=True, cwd=tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr
