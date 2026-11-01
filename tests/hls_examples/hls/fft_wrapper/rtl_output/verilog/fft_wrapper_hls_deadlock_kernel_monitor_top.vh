
wire kernel_monitor_reset;
wire kernel_monitor_clock;
wire kernel_monitor_report;
assign kernel_monitor_reset = ~ap_rst_n_clk;
assign kernel_monitor_clock = ap_clk;
assign kernel_monitor_report = 1'b0;
wire [1:0] axis_block_sigs;
wire [14:0] inst_idle_sigs;
wire [10:0] inst_block_sigs;
wire kernel_block;

assign axis_block_sigs[0] = ~store_sample_U0.input_signal_stream_TDATA_blk_n;
assign axis_block_sigs[1] = ~doFFT_U0.send_results_U0.fft_output_stream_TDATA_blk_n;

assign inst_idle_sigs[0] = store_sample_U0.ap_idle;
assign inst_block_sigs[0] = (store_sample_U0.ap_done & ~store_sample_U0.ap_continue);
assign inst_idle_sigs[1] = doFFT_U0.ap_idle;
assign inst_block_sigs[1] = (doFFT_U0.ap_done & ~doFFT_U0.ap_continue);
assign inst_idle_sigs[2] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_7_U0.ap_idle;
assign inst_block_sigs[2] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_7_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_7_U0.ap_continue);
assign inst_idle_sigs[3] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_6_U0.ap_idle;
assign inst_block_sigs[3] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_6_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_6_U0.ap_continue);
assign inst_idle_sigs[4] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_5_U0.ap_idle;
assign inst_block_sigs[4] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_5_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_5_U0.ap_continue);
assign inst_idle_sigs[5] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_4_U0.ap_idle;
assign inst_block_sigs[5] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_4_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_4_U0.ap_continue);
assign inst_idle_sigs[6] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_3_U0.ap_idle;
assign inst_block_sigs[6] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_3_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_3_U0.ap_continue);
assign inst_idle_sigs[7] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_2_U0.ap_idle;
assign inst_block_sigs[7] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_2_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_2_U0.ap_continue);
assign inst_idle_sigs[8] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_1_U0.ap_idle;
assign inst_block_sigs[8] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_1_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_1_U0.ap_continue);
assign inst_idle_sigs[9] = doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_U0.ap_idle;
assign inst_block_sigs[9] = (doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_U0.ap_done & ~doFFT_U0.ButterflyOperator_complex_ap_fixed_complex_ap_fixed_32_10_4_0_0_U0.ap_continue);
assign inst_idle_sigs[10] = doFFT_U0.send_results_U0.ap_idle;
assign inst_block_sigs[10] = (doFFT_U0.send_results_U0.ap_done & ~doFFT_U0.send_results_U0.ap_continue);

assign inst_idle_sigs[11] = 1'b0;
assign inst_idle_sigs[12] = store_sample_U0.ap_idle;
assign inst_idle_sigs[13] = doFFT_U0.ap_idle;
assign inst_idle_sigs[14] = doFFT_U0.send_results_U0.ap_idle;

fft_wrapper_hls_deadlock_idx0_monitor fft_wrapper_hls_deadlock_idx0_monitor_U (
    .clock(kernel_monitor_clock),
    .reset(kernel_monitor_reset),
    .axis_block_sigs(axis_block_sigs),
    .inst_idle_sigs(inst_idle_sigs),
    .inst_block_sigs(inst_block_sigs),
    .block(kernel_block)
);


always @ (kernel_block or kernel_monitor_reset) begin
    if (kernel_block == 1'b1 && kernel_monitor_reset == 1'b0) begin
        find_kernel_block = 1'b1;
    end
    else begin
        find_kernel_block = 1'b0;
    end
end
