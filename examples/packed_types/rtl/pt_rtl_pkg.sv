// Types the RTL declares. The fw-hdl design uses them too: emitted code
// refers to these declarations (pt_rtl_pkg::cmd_t), never a copy.
package pt_rtl_pkg;
    typedef enum logic [1:0] {OP_NOP, OP_ADD, OP_SUB, OP_CLR} op_e;
    typedef struct packed {
        op_e        op;
        logic [7:0] arg;
    } cmd_t;
endpackage
