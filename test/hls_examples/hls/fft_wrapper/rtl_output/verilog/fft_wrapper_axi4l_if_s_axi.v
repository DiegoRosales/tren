// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2024.1 (64-bit)
// Tool Version Limit: 2024.05
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2024 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
`timescale 1ns/1ps
module fft_wrapper_axi4l_if_s_axi
#(parameter
    C_S_AXI_ADDR_WIDTH = 10,
    C_S_AXI_DATA_WIDTH = 32
)(
    input  wire                          ACLK,
    input  wire                          ARESET,
    input  wire                          ACLK_EN,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] AWADDR,
    input  wire                          AWVALID,
    output wire                          AWREADY,
    input  wire [C_S_AXI_DATA_WIDTH-1:0] WDATA,
    input  wire [C_S_AXI_DATA_WIDTH/8-1:0] WSTRB,
    input  wire                          WVALID,
    output wire                          WREADY,
    output wire [1:0]                    BRESP,
    output wire                          BVALID,
    input  wire                          BREADY,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] ARADDR,
    input  wire                          ARVALID,
    output wire                          ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] RDATA,
    output wire [1:0]                    RRESP,
    output wire                          RVALID,
    input  wire                          RREADY,
    output wire                          interrupt,
    input  wire                          clk,
    input  wire                          rst,
    input  wire [6:0]                    window_coeffs_address0,
    input  wire                          window_coeffs_ce0,
    output wire [17:0]                   window_coeffs_q0,
    output wire                          ap_start,
    input  wire                          ap_done,
    input  wire                          ap_ready,
    input  wire                          ap_idle
);
//------------------------Address Info-------------------
// Protocol Used: ap_ctrl_hs
//
// 0x000 : Control signals
//         bit 0  - ap_start (Read/Write/COH)
//         bit 1  - ap_done (Read/COR)
//         bit 2  - ap_idle (Read)
//         bit 3  - ap_ready (Read/COR)
//         bit 7  - auto_restart (Read/Write)
//         bit 9  - interrupt (Read)
//         others - reserved
// 0x004 : Global Interrupt Enable Register
//         bit 0  - Global Interrupt Enable (Read/Write)
//         others - reserved
// 0x008 : IP Interrupt Enable Register (Read/Write)
//         bit 0 - enable ap_done interrupt (Read/Write)
//         bit 1 - enable ap_ready interrupt (Read/Write)
//         others - reserved
// 0x00c : IP Interrupt Status Register (Read/TOW)
//         bit 0 - ap_done (Read/TOW)
//         bit 1 - ap_ready (Read/TOW)
//         others - reserved
// 0x200 ~
// 0x3ff : Memory 'window_coeffs' (128 * 18b)
//         Word n : bit [17:0] - window_coeffs[n]
//                  others     - reserved
// (SC = Self Clear, COR = Clear on Read, TOW = Toggle on Write, COH = Clear on Handshake)

//------------------------Parameter----------------------
localparam
    ADDR_AP_CTRL            = 10'h000,
    ADDR_GIE                = 10'h004,
    ADDR_IER                = 10'h008,
    ADDR_ISR                = 10'h00c,
    ADDR_WINDOW_COEFFS_BASE = 10'h200,
    ADDR_WINDOW_COEFFS_HIGH = 10'h3ff,
    WRIDLE                  = 2'd0,
    WRDATA                  = 2'd1,
    WRRESP                  = 2'd2,
    WRRESET                 = 2'd3,
    RDIDLE                  = 2'd0,
    RDDATA                  = 2'd1,
    RDRESET                 = 2'd2,
    ADDR_BITS                = 10;

//------------------------Local signal-------------------
    reg  [1:0]                    wstate = WRRESET;
    reg  [1:0]                    wnext;
    reg  [ADDR_BITS-1:0]          waddr;
    wire [C_S_AXI_DATA_WIDTH-1:0] wmask;
    wire                          aw_hs;
    wire                          w_hs;
    reg  [1:0]                    rstate = RDRESET;
    reg  [1:0]                    rnext;
    reg  [C_S_AXI_DATA_WIDTH-1:0] rdata;
    wire                          ar_hs;
    wire [ADDR_BITS-1:0]          raddr;
    // internal registers
    reg                           int_ap_idle = 1'b0;
    reg                           ap_idle_get = 1'b0;
    reg                           int_ap_ready = 1'b0;
    wire                          task_ap_ready;
    reg                           ap_ready_get = 1'b0;
    reg                           ap_ready_ext = 1'b0;
    reg                           int_ap_done = 1'b0;
    wire                          task_ap_done;
    reg                           int_task_ap_done = 1'b0;
    reg                           task_ap_done_get = 1'b0;
    reg                           task_ap_done_ext = 1'b0;
    reg                           int_ap_start = 1'b0;
    reg                           ap_start_set = 1'b0;
    reg                           ap_start_mask = 1'b0;
    reg                           ap_start_get = 1'b0;
    reg                           int_interrupt = 1'b0;
    reg                           int_auto_restart = 1'b0;
    reg                           auto_restart_status = 1'b0;
    wire                          auto_restart_done;
    reg                           auto_restart_set = 1'b0;
    reg                           int_gie = 1'b0;
    reg  [1:0]                    int_ier = 2'b0;
    reg  [1:0]                    int_isr = 2'b0;
    reg  [1:0]                    isr_set = 2'b0;
    reg                           isr_clear;
    reg                           isr_mask;
    reg  [1:0]                    isr_wdata = 2'b0;
    // memory signals
    wire [6:0]                    int_window_coeffs_address0;
    wire                          int_window_coeffs_ce0;
    wire [31:0]                   int_window_coeffs_q0;
    wire [6:0]                    int_window_coeffs_address1;
    wire                          int_window_coeffs_ce1;
    wire                          int_window_coeffs_we1;
    wire [3:0]                    int_window_coeffs_be1;
    wire [31:0]                   int_window_coeffs_d1;
    wire [31:0]                   int_window_coeffs_q1;
    reg                           int_window_coeffs_read;
    reg                           int_window_coeffs_write;

//------------------------Instantiation------------------
// int_window_coeffs
fft_wrapper_axi4l_if_s_axi_ram #(
    .MEM_STYLE ( "auto" ),
    .MEM_TYPE  ( "2P" ),
    .BYTES     ( 4 ),
    .DEPTH     ( 128 )
) int_window_coeffs (
    .clk0      ( clk ),
    .address0  ( int_window_coeffs_address0 ),
    .ce0       ( int_window_coeffs_ce0 ),
    .we0       ( {4{1'b0}} ),
    .d0        ( {18{1'b0}} ),
    .q0        ( int_window_coeffs_q0 ),
    .clk1      ( ACLK ),
    .address1  ( int_window_coeffs_address1 ),
    .ce1       ( int_window_coeffs_ce1 ),
    .we1       ( int_window_coeffs_be1 ),
    .d1        ( int_window_coeffs_d1 ),
    .q1        ( int_window_coeffs_q1 )
);


//------------------------AXI write fsm------------------
assign AWREADY = (wstate == WRIDLE);
assign WREADY  = (wstate == WRDATA) && (!ar_hs);
assign BRESP   = 2'b00;  // OKAY
assign BVALID  = (wstate == WRRESP);
assign wmask   = { {8{WSTRB[3]}}, {8{WSTRB[2]}}, {8{WSTRB[1]}}, {8{WSTRB[0]}} };
assign aw_hs   = AWVALID & AWREADY;
assign w_hs    = WVALID & WREADY;

// wstate
always @(posedge ACLK) begin
    if (ARESET)
        wstate <= WRRESET;
    else if (ACLK_EN)
        wstate <= wnext;
end

// wnext
always @(*) begin
    case (wstate)
        WRIDLE:
            if (AWVALID)
                wnext = WRDATA;
            else
                wnext = WRIDLE;
        WRDATA:
            if (w_hs)
                wnext = WRRESP;
            else
                wnext = WRDATA;
        WRRESP:
            if (BREADY)
                wnext = WRIDLE;
            else
                wnext = WRRESP;
        default:
            wnext = WRIDLE;
    endcase
end

// waddr
always @(posedge ACLK) begin
    if (ACLK_EN) begin
        if (aw_hs)
            waddr <= {AWADDR[ADDR_BITS-1:2], {2{1'b0}}};
    end
end

//------------------------AXI read fsm-------------------
assign ARREADY = (rstate == RDIDLE);
assign RDATA   = rdata;
assign RRESP   = 2'b00;  // OKAY
assign RVALID  = (rstate == RDDATA) & !int_window_coeffs_read;
assign ar_hs   = ARVALID & ARREADY;
assign raddr   = ARADDR[ADDR_BITS-1:0];

// rstate
always @(posedge ACLK) begin
    if (ARESET)
        rstate <= RDRESET;
    else if (ACLK_EN)
        rstate <= rnext;
end

// rnext
always @(*) begin
    case (rstate)
        RDIDLE:
            if (ARVALID)
                rnext = RDDATA;
            else
                rnext = RDIDLE;
        RDDATA:
            if (RREADY & RVALID)
                rnext = RDIDLE;
            else
                rnext = RDDATA;
        default:
            rnext = RDIDLE;
    endcase
end

// rdata
always @(posedge ACLK) begin
    if (ACLK_EN) begin
        if (ar_hs) begin
            rdata <= 'b0;
            case (raddr)
                ADDR_AP_CTRL: begin
                    rdata[0] <= ap_start_get;
                    rdata[1] <= task_ap_done_get;
                    rdata[2] <= ap_idle_get;
                    rdata[3] <= ap_ready_get;
                    rdata[7] <= auto_restart_set;
                    rdata[9] <= int_interrupt;
                end
                ADDR_GIE: begin
                    rdata <= int_gie;
                end
                ADDR_IER: begin
                    rdata <= int_ier;
                end
                ADDR_ISR: begin
                    rdata <= int_isr;
                end
            endcase
        end
        else if (int_window_coeffs_read) begin
            rdata <= int_window_coeffs_q1;
        end
    end
end


//------------------------Register logic-----------------
assign interrupt         = int_interrupt;
assign ap_start          = int_ap_start;
assign task_ap_done      = (ap_done && !auto_restart_status) || auto_restart_done;
assign task_ap_ready     = ap_ready && !int_auto_restart;
assign auto_restart_done = auto_restart_status && (ap_idle && !int_ap_idle);
// int_interrupt
always @(posedge ACLK) begin
    if (ARESET)
        int_interrupt <= 1'b0;
    else if (ACLK_EN) begin
        if (int_gie && (|int_isr))
            int_interrupt <= 1'b1;
        else
            int_interrupt <= 1'b0;
    end
end

// ap_start_set
always @(posedge ACLK) begin
    if (ARESET)
        ap_start_set <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_AP_CTRL && WSTRB[0] && WDATA[0])
            ap_start_set <= 1'b1;
        else
            ap_start_set <= 1'b0; // self clear
    end
end

// ap_start_get
always @(posedge ACLK) begin
    if (ARESET)
        ap_start_get <= 1'b0;
    else if (ACLK_EN) begin
        if (int_ap_start)
            ap_start_get <= 1'b1;
        else
            ap_start_get <= 1'b0; // self clear
    end
end

// task_ap_done_get
always @(posedge ACLK) begin
    if (ARESET)
        task_ap_done_get <= 1'b0;
    else if (ACLK_EN) begin
        if (ar_hs && raddr == ADDR_AP_CTRL)
            task_ap_done_get <= 1'b0; // clear on read
        else if (int_task_ap_done)
            task_ap_done_get <= 1'b1;
    end
end

// ap_idle_get
always @(posedge ACLK) begin
    if (ARESET)
        ap_idle_get <= 1'b0;
    else if (ACLK_EN) begin
            ap_idle_get <= int_ap_idle;
    end
end

// ap_ready_get
always @(posedge ACLK) begin
    if (ARESET)
        ap_ready_get <= 1'b0;
    else if (ACLK_EN) begin
        if (ar_hs && raddr == ADDR_AP_CTRL)
            ap_ready_get <= 1'b0;
        else if (int_ap_ready)
            ap_ready_get <= 1'b1;
    end
end

// auto_restart_set
always @(posedge ACLK) begin
    if (ARESET)
        auto_restart_set <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_AP_CTRL && WSTRB[0])
            auto_restart_set <= WDATA[7];
    end
end

// int_gie
always @(posedge ACLK) begin
    if (ARESET)
        int_gie <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_GIE && WSTRB[0])
            int_gie <= WDATA[0];
    end
end

// int_ier
always @(posedge ACLK) begin
    if (ARESET)
        int_ier <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_IER && WSTRB[0])
            int_ier <= WDATA[1:0];
    end
end

// isr_wdata
always @(posedge ACLK) begin
    if (ARESET)
        isr_wdata <= 'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_ISR)
            isr_wdata <= WDATA[1:0];
    end
end

// isr_clear
always @(posedge ACLK) begin
    if (ARESET)
        isr_clear <= 1'b0;
    else if (ACLK_EN) begin
        if (w_hs && waddr == ADDR_ISR && WSTRB[0])
            isr_clear <= 1'b1;
        else
            isr_clear <= 1'b0; // self clear
    end
end

// int_isr
always @(posedge ACLK) begin
    if (ARESET)
        int_isr <= 'b0;
    else if (ACLK_EN) begin
            int_isr <= isr_set;
    end
end

// ap_start_mask
always @(posedge clk) begin
    if (rst)
        ap_start_mask <= 1'b0;
    else
        ap_start_mask <= ap_start_set;
end
// int_ap_start
always @(posedge clk) begin
    if (rst)
        int_ap_start <= 1'b0;
    else if (ap_start_set == 1'b1 && ap_start_mask == 1'b0)
        int_ap_start <= 1'b1;
    else if (ap_ready)
        int_ap_start <= int_auto_restart; // clear on handshake/auto restart
end
// int_ap_done
always @(posedge clk) begin
    if (rst)
        int_ap_done <= 1'b0;
    else
        int_ap_done <= ap_done;
end
// int_task_ap_done
always @(posedge clk) begin
    if (rst)
        int_task_ap_done <= 1'b0;
    else if (task_ap_done)
        int_task_ap_done <= 1'b1;
    else if (task_ap_done_get == 1'b0 && task_ap_done_ext == 1'b1)
        int_task_ap_done <= 1'b0;
end
// task_ap_done_ext
always @(posedge clk) begin
    if (rst)
        task_ap_done_ext <= 1'b0;
    else
        task_ap_done_ext <= task_ap_done_get;
end
// int_ap_idle
always @(posedge clk) begin
    if (rst)
        int_ap_idle <= 1'b0;
    else
        int_ap_idle <= ap_idle;
end
// ap_ready_ext
always @(posedge clk) begin
    if (rst)
        ap_ready_ext <= 1'b0;
    else
        ap_ready_ext <= ap_ready_get;
end
// int_ap_ready
always @(posedge clk) begin
    if (rst)
        int_ap_ready <= 1'b0;
    else if (task_ap_ready)
        int_ap_ready <= 1'b1;
    else if (ap_ready_get == 1'b0 && ap_ready_ext == 1'b1)
        int_ap_ready <= 1'b0;
end
// int_auto_restart
always @(posedge clk) begin
    if (rst)
        int_auto_restart <= 1'b0;
    else
        int_auto_restart <= auto_restart_set;
end
// auto_restart_status
always @(posedge clk) begin
    if (rst)
        auto_restart_status <= 1'b0;
    else if (int_auto_restart)
        auto_restart_status <= 1'b1;
    else if (ap_idle)
        auto_restart_status <= 1'b0;
end
// isr_mask
always @(posedge clk) begin
    if (rst)
        isr_mask <= 1'b0;
    else
        isr_mask <= isr_clear;
end
// isr_set[0]
always @(posedge clk) begin
    if (rst)
        isr_set[0] <= 1'b0;
    else if (int_ier[0] & ap_done)
        isr_set[0] <= 1'b1;
    else if (isr_clear == 1'b1 && isr_mask == 1'b0)
        isr_set[0] <=  isr_set[0] ^ isr_wdata[0]; // toggle on write
end
// isr_set[1]
always @(posedge clk) begin
    if (rst)
        isr_set[1] <= 1'b0;
    else if (int_ier[1] & ap_ready)
        isr_set[1] <= 1'b1;
    else if (isr_clear == 1'b1 && isr_mask == 1'b0)
        isr_set[1] <=  isr_set[1] ^ isr_wdata[1]; // toggle on write
end
//synthesis translate_off
always @(posedge clk) begin
    if (int_gie & ~isr_set[0] & int_ier[0] & ap_done)
        $display ("// Interrupt Monitor : interrupt for ap_done detected @ \"%0t\"", $time);
    if (int_gie & ~isr_set[1] & int_ier[1] & ap_ready)
        $display ("// Interrupt Monitor : interrupt for ap_ready detected @ \"%0t\"", $time);
end
//synthesis translate_on

//------------------------Memory logic-------------------
// window_coeffs
assign int_window_coeffs_address0 = window_coeffs_address0;
assign int_window_coeffs_ce0      = window_coeffs_ce0;
assign window_coeffs_q0           = int_window_coeffs_q0;
assign int_window_coeffs_address1 = ar_hs? raddr[8:2] : waddr[8:2];
assign int_window_coeffs_ce1      = ar_hs | (int_window_coeffs_write & WVALID);
assign int_window_coeffs_we1      = int_window_coeffs_write & w_hs;
assign int_window_coeffs_be1      = int_window_coeffs_we1 ? WSTRB : 'b0;
assign int_window_coeffs_d1       = WDATA;
// int_window_coeffs_read
always @(posedge ACLK) begin
    if (ARESET)
        int_window_coeffs_read <= 1'b0;
    else if (ACLK_EN) begin
        if (ar_hs && raddr >= ADDR_WINDOW_COEFFS_BASE && raddr <= ADDR_WINDOW_COEFFS_HIGH)
            int_window_coeffs_read <= 1'b1;
        else
            int_window_coeffs_read <= 1'b0;
    end
end

// int_window_coeffs_write
always @(posedge ACLK) begin
    if (ARESET)
        int_window_coeffs_write <= 1'b0;
    else if (ACLK_EN) begin
        if (aw_hs && AWADDR[ADDR_BITS-1:0] >= ADDR_WINDOW_COEFFS_BASE && AWADDR[ADDR_BITS-1:0] <= ADDR_WINDOW_COEFFS_HIGH)
            int_window_coeffs_write <= 1'b1;
        else if (w_hs)
            int_window_coeffs_write <= 1'b0;
    end
end


endmodule


`timescale 1ns/1ps

module fft_wrapper_axi4l_if_s_axi_ram
#(parameter
    MEM_STYLE = "auto",
    MEM_TYPE  = "S2P",
    BYTES  = 4,
    DEPTH  = 256,
    AWIDTH = log2(DEPTH)
) (
    input  wire               clk0,
    input  wire [AWIDTH-1:0]  address0,
    input  wire               ce0,
    input  wire [BYTES-1:0]   we0,
    input  wire [BYTES*8-1:0] d0,
    output reg  [BYTES*8-1:0] q0,
    input  wire               clk1,
    input  wire [AWIDTH-1:0]  address1,
    input  wire               ce1,
    input  wire [BYTES-1:0]   we1,
    input  wire [BYTES*8-1:0] d1,
    output reg  [BYTES*8-1:0] q1
);
//------------------------ Parameters -------------------
localparam
    BYTE_WIDTH = 8,
    PORT0 = (MEM_TYPE == "S2P") ? "WO" : ((MEM_TYPE == "2P") ? "RO" : "RW"),
    PORT1 = (MEM_TYPE == "S2P") ? "RO" : "RW";
//------------------------Local signal-------------------
(* ram_style = MEM_STYLE*)
reg  [BYTES*8-1:0] mem[0:DEPTH-1];
wire re0, re1;
//------------------------Task and function--------------
function integer log2;
    input integer x;
    integer n, m;
begin
    n = 1;
    m = 2;
    while (m < x) begin
        n = n + 1;
        m = m * 2;
    end
    log2 = n;
end
endfunction
//------------------------Body---------------------------
generate
    if (MEM_STYLE == "hls_ultra" && PORT0 == "RW") begin
        assign re0 = ce0 & ~|we0;
    end else begin
        assign re0 = ce0;
    end
endgenerate

generate
    if (MEM_STYLE == "hls_ultra" && PORT1 == "RW") begin
        assign re1 = ce1 & ~|we1;
    end else begin
        assign re1 = ce1;
    end
endgenerate

// read port 0
generate if (PORT0 != "WO") begin
    always @(posedge clk0) begin
        if (re0) q0 <= mem[address0];
    end
end
endgenerate

// read port 1
generate if (PORT1 != "WO") begin
    always @(posedge clk1) begin
        if (re1) q1 <= mem[address1];
    end
end
endgenerate

integer i;
// write port 0
generate if (PORT0 != "RO") begin
    always @(posedge clk0) begin
        if (ce0)
        for (i = 0; i < BYTES; i = i + 1)
            if (we0[i])
                mem[address0][i*BYTE_WIDTH +: BYTE_WIDTH] <= d0[i*BYTE_WIDTH +: BYTE_WIDTH];
    end
end
endgenerate

// write port 1
generate if (PORT1 != "RO") begin
    always @(posedge clk1) begin
        if (ce1)
        for (i = 0; i < BYTES; i = i + 1)
            if (we1[i])
                mem[address1][i*BYTE_WIDTH +: BYTE_WIDTH] <= d1[i*BYTE_WIDTH +: BYTE_WIDTH];
    end
end
endgenerate

endmodule

