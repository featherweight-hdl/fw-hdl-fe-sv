// An accumulator that takes a command struct of the RTL's own type. nops,
// a debug count whose width follows W, is left open by the wrapper.
module pt_acc import pt_rtl_pkg::*; #(
    parameter int W = 8
) (
    input  logic              clk,
    input  logic              rst,
    input  cmd_t              cmd,
    output logic [W-1:0]      acc,
    output logic [$clog2(W):0] nops
);
    always_ff @(posedge clk) begin
        if (rst) begin
            acc  <= '0;
            nops <= '0;
        end else begin
            case (cmd.op)
                OP_ADD:  acc <= acc + W'(cmd.arg);
                OP_SUB:  acc <= acc - W'(cmd.arg);
                OP_CLR:  acc <= '0;
                default: nops <= nops + 1'b1;
            endcase
        end
    end
endmodule
