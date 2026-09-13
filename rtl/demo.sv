// demo.sv - small hierarchical SystemVerilog design for evaluating TREN
// elaboration backends.
//
// Exercises, on purpose:
//   - parametrised modules with overrides (core#(32), alu#(W))
//   - a generate block instantiating an array of modules (u_alu_g[0..1])
//   - part-selects across a hierarchy boundary (o[31:0], o[63:32])
//   - an always_comb driver inside alu (the cell that owns `result`)
//   - an opaque leaf module (sram_stub: ports, no body) - black box handling
//
// Instance tree:
//   top
//    |- o                      logic[63:0], driven by concat {hi, lo}
//    `- u_core                 core #(W=32)
//        |- u_alu0             alu #(W=32)            result -> r0
//        |- u_alu_g            alu #(W=32), x2        (generate block g; each
//        |                      genblk has a local net gr, so no multi-driver)
//        |- u_sram             sram_stub    (black box)
//        `- always_comb        alu_result = r0 ^ x    (single driver, fan-in)

module alu #(parameter int unsigned W = 8)
  (input  logic [W-1:0] a,
   input  logic [W-1:0] b,
   input  logic [3:0]   op,
   output logic [W-1:0] result);

  always_comb begin
    unique case (op)
      0:       result = a + b;
      1:       result = a - b;
      2:       result = a & b;
      default: result = a ^ b;
    endcase
  end
endmodule

// Black-box leaf: ports only, no body.
module sram_stub
  (input  logic [31:0] addr,
   input  logic [31:0] wdata,
   input  logic        we,
   output logic [31:0] rdata);
endmodule

module core #(parameter int unsigned W = 8)
  (input  logic [W-1:0] a,
   input  logic [W-1:0] b,
   output logic [W-1:0] alu_result);

  logic [W-1:0] x, y, rd, r0;
  localparam logic [3:0] OP = 3;

  assign x = a;
  assign y = b;

  alu #(W) u_alu0(.a(x), .b(y), .op(OP), .result(r0));

  for (genvar i = 0; i < 2; i++) begin : g
    logic [W-1:0] gr;
    alu #(W) u_alu_g(.a(x), .b(y), .op(OP), .result(gr));
  end

  always_comb begin
    alu_result = r0 ^ x;
  end

  sram_stub u_sram(.addr(x), .wdata(y), .we(1'b0), .rdata(rd));
endmodule

module top
  (input  logic [31:0] hi,
   input  logic [31:0] lo,
   output logic [31:0] out);

  logic [63:0] o;
  assign o = {hi, lo};
  core #(32) u_core(.a(o[31:0]), .b(o[63:32]), .alu_result(out));
endmodule