// Drives one ready/valid lane of a generated module and of its reference
// with the same random stimulus, and checks, on every cycle, that their
// outputs match, and that values leave in the order they entered.
module rv_lane_check #(
    parameter int          W    = 8,
    parameter int unsigned SEED = 1
) (
    input  logic         clk, rst,
    output logic         in_valid,
    output logic [W-1:0] in_data,
    output logic         out_ready,
    input  logic         g_in_ready, r_in_ready,
    input  logic         g_out_valid, r_out_valid,
    input  logic [W-1:0] g_out_data, r_out_data,
    output int           errors,
    output int           recv
);
    int unsigned seed = SEED;
    function automatic int unsigned rnd();
        seed = seed * 1103515245 + 12345;
        return seed >> 16;
    endfunction

    logic [W-1:0] expect_q[$];
    logic [W-1:0] next_val = 0;

    initial begin
        in_valid = 0; in_data = 0; out_ready = 0; errors = 0; recv = 0;
    end

    always @(posedge clk) begin
        if (!rst) begin
            if (g_in_ready !== r_in_ready || g_out_valid !== r_out_valid ||
                    (r_out_valid && g_out_data !== r_out_data)) begin
                $display("MISMATCH %m t=%0t in_ready %b/%b out_valid %b/%b out_data %h/%h",
                         $time, g_in_ready, r_in_ready, g_out_valid, r_out_valid,
                         g_out_data, r_out_data);
                errors <= errors + 1;
            end
            if (in_valid && g_in_ready)
                expect_q.push_back(in_data);
            if (g_out_valid && out_ready) begin
                if (expect_q.size() == 0 || expect_q[0] !== g_out_data) begin
                    $display("ORDER %m t=%0t got %h", $time, g_out_data);
                    errors <= errors + 1;
                end else
                    void'(expect_q.pop_front());
                recv <= recv + 1;
            end
            if (!in_valid || g_in_ready) begin
                in_valid <= (rnd() % 4) != 0;
                in_data  <= next_val;
                next_val = next_val + 1;
            end
            out_ready <= (rnd() % 3) != 0;
        end
    end
endmodule
