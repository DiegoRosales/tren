// missing_rtl.sv - a SystemVerilog design whose vendored module is absent.
//
// The design uses a streaming operator, so `read_verilog` cannot parse it, and
// it instantiates `vendor_ip`, which no source in this set declares. Both at
// once is the case that used to fail: `read_slang` needs a declaration for
// every instantiated module. TREN infers a black box from the instantiation
// and the design elaborates.
//
// The inferred box is not a model of the real IP: its port order, names and
// widths come from this instantiation, and every port direction is unknown.

module missing_rtl
  (input  logic [7:0] d,
   input  logic [3:0] nib,
   output logic [7:0] q,
   output logic [3:0] n);

  logic [7:0] t;
  logic [3:0] u;

  // 8-bit ports and one expression, the common vendor-IP shape
  vendor_ip #(.W(8)) u_ip (.din(t), .dout(q), .sel(nib == 4'd1));

  // positional connections: TREN names them p0, p1
  vendor_pos u_pos (d, u);

  assign t = {<<8{d}};
  assign n = u;
endmodule
