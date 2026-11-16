// Runs the generated soc and the hand-wired soc_ref side by side on the
// same random stimulus: values enter on clk_a and leave on clk_b, two
// unrelated clocks. Requires in_ready to match on every clk_a edge and the
// outputs on every clk_b edge, and the stream to leave in the order it
// entered.
module soc_tb;
    logic       clk_a = 0, clk_b = 0, rst_n = 0;
    logic       in_valid = 0, out_ready = 0;
    logic [7:0] in_data = 0;

    logic       g_in_ready, g_out_valid, r_in_ready, r_out_valid;
    logic [7:0] g_out_data, r_out_data;

    soc dut (
        .clk_a_i(clk_a), .clk_b_i(clk_b), .rst_ni(rst_n),
        .in_valid(in_valid), .in_ready(g_in_ready), .in_data(in_data),
        .out_valid(g_out_valid), .out_ready(out_ready), .out_data(g_out_data));

    soc_ref ref_ (
        .clk_a_i(clk_a), .clk_b_i(clk_b), .rst_ni(rst_n),
        .in_valid(in_valid), .in_ready(r_in_ready), .in_data(in_data),
        .out_valid(r_out_valid), .out_ready(out_ready), .out_data(r_out_data));

    always #5 clk_a = ~clk_a;
    always #7 clk_b = ~clk_b;

    int unsigned seed_a = 1, seed_b = 7;
    function automatic int unsigned rnd(ref int unsigned seed);
        seed = seed * 1103515245 + 12345;
        return seed >> 16;
    endfunction

    logic [7:0] expect_q[$];
    int errors = 0, sent = 0, recv = 0;
    bit done = 0;

    // The clk_a side: offer a new value when the last one was taken.
    initial begin
        repeat (4) @(posedge clk_a);
        rst_n <= 1;
        repeat (3000) begin
            @(posedge clk_a);
            if (g_in_ready !== r_in_ready) begin
                $display("MISMATCH t=%0t in_ready %b/%b", $time, g_in_ready, r_in_ready);
                errors++;
            end
            if (in_valid && g_in_ready) begin
                expect_q.push_back(in_data);
                sent++;
                in_data <= in_data + 1;
            end
            if (!in_valid || g_in_ready)
                in_valid <= (rnd(seed_a) % 4) != 0;
        end
        in_valid <= 0;
        repeat (200) @(posedge clk_a);
        done = 1;
    end

    // The clk_b side: compare with the reference, score the stream.
    always @(posedge clk_b) begin
        if (rst_n) begin
            if (g_out_valid !== r_out_valid || (r_out_valid && g_out_data !== r_out_data)) begin
                $display("MISMATCH t=%0t out_valid %b/%b out_data %h/%h", $time,
                         g_out_valid, r_out_valid, g_out_data, r_out_data);
                errors++;
            end
            if (g_out_valid && out_ready) begin
                if (expect_q.size() == 0 || expect_q[0] !== g_out_data) begin
                    $display("ORDER t=%0t got %h", $time, g_out_data);
                    errors++;
                end else
                    void'(expect_q.pop_front());
                recv++;
            end
            out_ready <= (rnd(seed_b) % 3) != 0;
        end
    end

    initial begin
        wait (done);
        if (errors == 0 && recv > 100 && recv == sent)
            $display("PASS: %0d sent, %0d received, outputs match the reference", sent, recv);
        else
            $display("FAIL: %0d errors, %0d sent, %0d received", errors, sent, recv);
        $finish;
    end
endmodule
