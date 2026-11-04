// generate.sv - generate constructs for the elaborator acceptance drive.
//
// Exercises a named generate-for array, a named generate-if with an inactive
// else branch, an anonymous generate-if, a generate-case with an inactive
// default, a nested generate, a zero-width generate-for, local declarations
// and a parameter override on every leaf instance.

module gen_leaf #(parameter int W = 4)
  (input  logic [W-1:0] a,
   output logic [W-1:0] y);

  assign y = a;
endmodule

module gen_top #(parameter int N = 2,
                 parameter int USE_IF = 1,
                 parameter int SEL = 0)
  (input  logic [7:0] a,
   output logic [7:0] y);

  for (genvar i = 0; i < N; i++) begin : garr
    logic [7:0] t;
    gen_leaf #(.W(8)) u_leaf(.a(a), .y(t));
  end

  if (USE_IF) begin : g_if
    logic [7:0] t;
    gen_leaf #(.W(8)) u_if(.a(a), .y(t));
  end else begin : g_else
    gen_leaf #(.W(8)) u_else(.a(a), .y());
  end

  if (N > 0) begin
    logic [7:0] t;
    gen_leaf #(.W(8)) u_anon(.a(a), .y(t));
  end

  case (SEL)
    0: begin : g_case0
      logic [7:0] t;
      gen_leaf #(.W(8)) u_case0(.a(a), .y(t));
    end
    default: begin : g_case_def
      gen_leaf #(.W(8)) u_case_def(.a(a), .y());
    end
  endcase

  for (genvar j = 0; j < 1; j++) begin : g_outer
    if (USE_IF) begin : g_inner
      logic [7:0] t;
      gen_leaf #(.W(8)) u_nested(.a(a), .y(t));
    end
  end

  for (genvar k = 0; k < 0; k++) begin : g_zero
    gen_leaf #(.W(8)) u_zero(.a(a), .y());
  end

  if (USE_IF) begin : g_decl
    logic [7:0] only;
    assign only = a;
  end

  assign y = a;
endmodule
