// Existing RTL that the example wraps: a divide-by-2 clock. clk_o toggles
// on every rising edge of clk_i; rst_ni (active low, asynchronous) holds it
// low.
module clk_div2 (
    input  logic clk_i,
    input  logic rst_ni,
    output logic clk_o
);
    logic q;

    always_ff @(posedge clk_i or negedge rst_ni)
        if (!rst_ni) q <= 1'b0;
        else         q <= ~q;

    assign clk_o = q;
endmodule
