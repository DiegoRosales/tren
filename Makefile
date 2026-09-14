# TREN — Vitis HLS example build flow.
#
# The flow mirrors ipyfuse's test/hls_examples: each block under
# test/hls_examples/hls/<block> is a Vitis HLS project directory with
# config.tcl (sources), directives.tcl, and a committed rtl_output/ snapshot
# (the impl/verilog export) so `tren import` works in a fresh clone without a
# Xilinx install. Rebuilding requires a Vitis/HLS installation:
#
#   make setup_xilinx                  # XILINX_VITIS=/path/to/Vitis/v2025.2  -> writes .xilinx_env
#   make fft_wrapper_HLS               # rebuild + export one block (vitis_hls -f run_hls.tcl <block>)
#   make hls_examples                  # rebuild everything
#
# The Xilinx tools run under the flake's #xilinx FHS sandbox (Xilinx binaries
# expect a conventional /usr/lib layout that NixOS lacks). The sandbox shell
# is interactive-only (nix develop -c does not reach it), so:
#
#   nix develop .#xilinx                 # enter the FHS sandbox
#   make setup_xilinx                    # XILINX_VITIS=/path/to/Vitis
#   make fft_wrapper_HLS                 # vitis_hls run_hls.tcl
#   exit
#
# .xilinx_env is gitignored (machine-specific settings64.sh path);
# rtl_output/ snapshots are committed so a fresh clone imports without a
# Xilinx install.

HLS_EXAMPLES_PATH := test/hls_examples

BLOCK_LIST += test_axis_interfaces
BLOCK_LIST += test_simple_interfaces
BLOCK_LIST += test_simple_interfaces2
BLOCK_LIST += test_simple_interfaces3
BLOCK_LIST += test_adder_2p
BLOCK_LIST += test_adder_4p
BLOCK_LIST += fft_wrapper
BLOCK_LIST += dma_codec_mux_wrapper

HLS_LIST := $(foreach BLOCK,$(BLOCK_LIST),$(BLOCK)_HLS)

## Evaluated on every make invocation, including targets that have nothing to
## do with Xilinx. .xilinx_env is gitignored, so it is absent in CI and in a
## fresh clone -- swallow the error rather than printing "cat: .xilinx_env: No
## such file or directory" ahead of every target. An empty value is the
## expected "not configured" state; the %_HLS rule below checks for it and
## prints a useful message.
XILINX_ENV := $(shell cat .xilinx_env 2>/dev/null)

%_HLS:
ifeq ($(strip $(XILINX_ENV)),)
	@echo "XILINX_ENV is not set; run 'make setup_xilinx' (with XILINX_VITIS defined) to create .xilinx_env first"
	@exit 1
endif
	echo $*
	source $(XILINX_ENV) ;\
	cd $(HLS_EXAMPLES_PATH)/hls ;\
	git clean -dfx ./$* ;\
	vitis_hls -f run_hls.tcl $*

hls_examples: $(HLS_LIST)

## Initial step. Source the settings64.sh from your Vitis/Vivado installation
## and run `make setup_xilinx` (inside `nix develop .#xilinx`) to create
## .xilinx_env.
setup_xilinx:
ifeq ($(strip $(XILINX_VITIS)),)
	@echo "XILINX_VITIS is not defined; set it to your Vitis install path (e.g. /tools/Xilinx/Vitis/2025.2)"
	@exit 1
else
	echo "$(XILINX_VITIS)/settings64.sh" > .xilinx_env
endif

.PHONY: hls_examples setup_xilinx