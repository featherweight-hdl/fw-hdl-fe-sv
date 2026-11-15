// fe-sv's fw_hdl_pkg (core-layer1.md §4.1): the spec's declarations,
// unchanged, plus out-of-block bodies that fe-sv generates when it loads
// the API. The bodies only make the package compile: the interpreter runs
// an intrinsic in place of every API method.
`include "fw_hdl_macros.svh"

package fw_hdl_pkg;
    `include "fw_hdl_api.svh"
    `include "fw_fe_sv_bodies.svh"
endpackage
