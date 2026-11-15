# fw-hdl-fe-sv

The Featherweight-HDL static front end. It elaborates a design written
against the fw-hdl SV API (`fw-hdl-spec`) without a simulator, by
interpreting its classes, and produces Zuspec IR. Zuspec passes and
`zuspec-be-sv` turn that into Verilog.

See `docs/design/core-layer1.md` in the fw-hdl umbrella repo, section 6.
