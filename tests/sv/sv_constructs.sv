// sv_constructs.sv - SystemVerilog that yosys `read_verilog` cannot parse.
//
// Exercises, on purpose:
//   - a package reached through `import` (read_verilog stops at TOK_IMPORT)
//   - a streaming operator, {<<8{...}}
//   - an assignment pattern with a default member, '{lo: ..., default: ...}
//   - one submodule, so the netlist still has a hierarchy to cross
//
// The elaboration netlist pass falls back to `read_slang` on this source set.
// `read_verilog` keeps the first try for every design it can parse, because
// it tolerates an instantiated module with no declaration.

package sv_constructs_pkg;
  typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
  typedef struct packed {
    logic [7:0] lo;
    logic [7:0] hi;
  } pair_t;
endpackage

module sv_leaf #(parameter int W = 8)
  (input  logic [W-1:0] a,
   output logic [W-1:0] y);

  assign y = a + 1'b1;
endmodule

module sv_constructs
  (input  logic        clk,
   input  logic [7:0]  d,
   output logic [7:0]  q,
   output logic [15:0] packed_out);

  import sv_constructs_pkg::*;

  state_t     state;
  pair_t      pr;
  logic [7:0] shadow;

  always_ff @(posedge clk)
    state <= (state == DONE) ? IDLE : state_t'(state + 1'b1);

  // streaming operator: reverse the byte order of d
  assign shadow = {<<8{d}};

  // assignment pattern with a default member: hi takes the default
  always_comb pr = '{lo: shadow, default: 8'h00};

  assign packed_out = pr;

  sv_leaf #(.W(8)) u_leaf (.a({6'b0, state}), .y(q));
endmodule
