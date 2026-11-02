`include "defs.svh"

module alu_top (
    input  logic [`TREN_WIDTH-1:0] a,
    input  logic [`TREN_WIDTH-1:0] b,
    output logic [`TREN_WIDTH-1:0] y
);
    alu #(.WIDTH(`TREN_WIDTH)) u_alu (
        .a (a),
        .b (b),
        .y (y)
    );
endmodule
