// A ready/valid pass-through: out is in, with no storage. The leaf that
// gaps_pkg::wwire wraps (the "no FIFO" arm of cond_pipe).
module rv_wire #(
    parameter int WIDTH = 8
) (
    input  logic [WIDTH-1:0] in_data,
    input  logic             in_vld,
    output logic             in_rdy,
    output logic [WIDTH-1:0] out_data,
    output logic             out_vld,
    input  logic             out_rdy
);
    assign out_data = in_data;
    assign out_vld  = in_vld;
    assign in_rdy   = out_rdy;
endmodule
