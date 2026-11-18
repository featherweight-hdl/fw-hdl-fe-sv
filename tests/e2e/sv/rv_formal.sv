// Formal harness for the lowered std fw_rv_mon_core (test_rv_formal). The
// link's pins are free; the source keeps a stalled beat (valid and data held
// until ready). The checkers: one post (fire) per handshake, a pre (first)
// before each post and only one per transfer, and the beat at an arbitrary
// index keeps the data it was offered with.
module rv_formal(input logic clk);
  (* anyseq *) logic valid, ready;
  (* anyseq *) logic [7:0] data;
  (* anyconst *) logic [3:0] idx;
  logic fire, first;
  fw_rv_mon_core #(.T_W(8)) dut(.clk(clk), .p_valid(valid), .p_ready(ready), .p_data(data),
                                .fire(fire), .first(first));
  reg f_past = 0, open = 0;
  reg [3:0] n = 0;
  reg [7:0] d0 = 0;
  always @(posedge clk) begin
    f_past <= 1;
    if (fire) begin open <= 0; n <= n + 1; end
    else if (first) open <= 1;
    if (first && n == idx) d0 <= data;
  end
  always @(*) begin
    if (!f_past) assume(!valid);                         // held has no reset
    assert(fire == (valid && ready));                    // one post per handshake
    if (fire) assert(first || open);                     // pre before post
    if (first) assert(!open);                            // one pre per transfer
    if (fire && !first && n == idx) assert(data == d0);  // data integrity
    cover(fire && !first && n == 2);                     // a stalled beat accepted
  end
  always @(posedge clk) if (f_past && $past(valid && !ready)) begin
    assume(valid);
    assume(data == $past(data));
  end
endmodule
