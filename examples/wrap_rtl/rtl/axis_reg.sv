// A full-throughput AXI-Stream register slice (a skid buffer): every
// output is registered, and s_axis_tready depends only on the slice's own
// state. Stands in for third-party RTL with its own pin conventions: AXI
// names, a `tlast` side band, and an active-low synchronous reset.
module axis_reg #(
    parameter int DATA_W = 8
) (
    input  logic              aclk,
    input  logic              aresetn,
    input  logic [DATA_W-1:0] s_axis_tdata,
    input  logic              s_axis_tvalid,
    output logic              s_axis_tready,
    input  logic              s_axis_tlast,
    output logic [DATA_W-1:0] m_axis_tdata,
    output logic              m_axis_tvalid,
    input  logic              m_axis_tready,
    output logic              m_axis_tlast
);
    logic [DATA_W-1:0] skid_data;
    logic              skid_last;
    logic              skid_vld;

    assign s_axis_tready = !skid_vld;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            m_axis_tvalid <= 1'b0;
            skid_vld      <= 1'b0;
        end else begin
            if (m_axis_tready || !m_axis_tvalid) begin
                // The output register is free: fill it from the skid
                // buffer first, else from the input.
                if (skid_vld) begin
                    m_axis_tdata  <= skid_data;
                    m_axis_tlast  <= skid_last;
                    m_axis_tvalid <= 1'b1;
                    skid_vld      <= 1'b0;
                end else begin
                    m_axis_tdata  <= s_axis_tdata;
                    m_axis_tlast  <= s_axis_tlast;
                    m_axis_tvalid <= s_axis_tvalid;
                end
            end else if (s_axis_tvalid && s_axis_tready) begin
                // Output stalled: park the accepted beat.
                skid_data <= s_axis_tdata;
                skid_last <= s_axis_tlast;
                skid_vld  <= 1'b1;
            end
        end
    end
endmodule
