# fw-hdl-fe-sv

The Featherweight-HDL static front end. It elaborates a design written
against the fw-hdl SV API (`fw-hdl-spec`) without a simulator, by
interpreting its classes, and produces Zuspec IR. Zuspec passes and
`zuspec-be-sv` turn that into Verilog.

See `docs/design/core-layer1.md` in the fw-hdl umbrella repo, section 6.

It implements the spec's optional `domains` and `protocols` features:
protocol registration and queries (`protocols.py`), API endpoints and
sockets, configuration publishers and consumers (`rt.py`, `cfgcheck.py`),
and domain crossings over those bindings (`domains.py`). The netlist
(`netlist.py`) carries the `protocols` table, `bindings`, and each
bundle's `protocol` and `role`; `ir_map.py` maps them to Zuspec IR.
Every scenario in `fw-hdl-spec/conformance` passes
(`python -m pytest tests/conformance`).

`scan.py` finds the protocols, bundles and converters in a collection of
sources with no top. The dfm package `fw.hdl.fe.sv` (`flow.dv`) has four
tasks: `Scan` writes `scan.json` (the `protocols` table) and `scan.zir`
(each protocol's API classes and their types); `Elaborate` takes sources
directly or after `Scan`, and then checks the scan against what it
elaborates; `EmitSV` turns the `.zir` into Verilog; `GenKit` scans a
protocol kit's sources and writes the classes derived from its APIs
(proxies, listeners, imps, transactions, pin-map presets, ...) as one
package,
`<core pkg>_gen_pkg.sv`, a `systemVerilogSource` FileSet. The generators
are in `python/fw/hdl/gen/` (`gen_kit`); `GenKit` re-runs when they change
(`GEN_VERSION`, a hash of their sources), not only when its inputs do.
