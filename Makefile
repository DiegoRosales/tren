# TREN — runnable examples + Vitis HLS example build flow.
#
# Everything in `make help` WORKS on a stock checkout, with nothing but the
# nix flake (every command runs under `nix develop . -c ...`, no host tools).
# The Vitis HLS section at the bottom additionally needs a Xilinx/Vitis
# install and the flake's `#xilinx` FHS sandbox.

NIX := nix develop . -c
HLS_EXAMPLES_PATH := test/hls_examples

.DEFAULT_GOAL := help

.PHONY: help run run-fixture import elaborate test drive drive-real roundtrip \
        pane-cue profile bom check hls-import hls-elaborate hls-run

help:
	@echo "TREN examples that work (stock checkout, nix-only):"
	@echo
	@echo "  make run            TUI on the real demo IR (rtl/demo.manifest.json)"
	@echo "  make run-fixture    TUI on the vendored demo fixture"
	@echo "  make import         regenerate rtl/manifest.json from rtl/ (dir scan)"
	@echo "  make elaborate      elaborate the demo manifest -> IR summary (cached)"
	@echo "  make test           unit tests"
	@echo "  make drive          headless Pilot drive, fixture mode"
	@echo "  make drive-real     headless Pilot drive, real IR + trace/black-box flows"
	@echo "  make roundtrip      real-terminal OSC 52 + editor round trip (private tmux server)"
	@echo "  make pane-cue       real-terminal check of the pane cue + path status line (private tmux server)"
	@echo "  make profile        lazy-load + cache profile on a synthetic 1100-scope IR"
	@echo "  make bom            regenerate docs/sw-bom.md from the dev shell (card 263)"
	@echo "  make check          unit + both drives (what CI runs)"
	@echo
	@echo "  make hls-import     import the HLS RTL export: test/hls_examples/hls/<BLOCK>/rtl_output"
	@echo "  make hls-elaborate  elaborate <BLOCK>'s HLS RTL into the IR (read-only, no Xilinx)"
	@echo "  make hls-run        TUI on <BLOCK>'s HLS RTL"
	@echo "                      (BLOCK defaults to fft_wrapper; e.g. make hls-elaborate BLOCK=test_adder_2p)"
	@echo
	@echo "Vitis HLS flow (needs a Xilinx install + 'nix develop .#xilinx'):"
	@echo "  make setup_xilinx   XILINX_VITIS=/path/to/Vitis"
	@echo "  make <block>_HLS    vitis_hls -f run_hls.tcl <block>   e.g. make fft_wrapper_HLS"
	@echo "  make hls_examples   rebuild every block"
	@echo

# --- interactive TUI (q quits) --------------------------------------------

run:
	$(NIX) bash -c 'PYTHONPATH=src:$$PYTHONPATH python -m tren.tui --manifest rtl/demo.manifest.json'

run-fixture:
	$(NIX) bash -c 'PYTHONPATH=src:$$PYTHONPATH python -m tren.tui'

# --- examples (all verified, non-interactive) -----------------------------

import:
	$(NIX) tren import rtl --out rtl/manifest.json

elaborate:
	$(NIX) tren elaborate rtl/demo.manifest.json

test:
	$(NIX) bash -c 'PYTHONPATH=src:$$PYTHONPATH python -m unittest discover -s tests'

drive:
	$(NIX) python test_drive.py

drive-real:
	$(NIX) python test_drive.py --manifest rtl/demo.manifest.json

roundtrip:
	$(NIX) bash tools/osc52_roundtrip.sh

pane-cue:
	$(NIX) bash tools/pane_cue_capture.sh

profile:
	$(NIX) python tools/profile_1k_scopes.py

bom:
	$(NIX) python tools/bom.py

check: test drive drive-real

# --- HLS RTL-output examples ----------------------------------------------
# Read-only reading of a committed rtl_output/ export (no Xilinx install
# needed): import the Verilog set to a manifest, then elaborate or browse it.
# Manifests land in .cache/hls (gitignored); BLOCK selects the example.

HLS_RTL_DIR = $(HLS_EXAMPLES_PATH)/hls
BLOCK ?= fft_wrapper
HLS_RTL_MANIFEST = .cache/hls/$(BLOCK).json

hls-import:
	@mkdir -p .cache/hls
	$(NIX) tren import $(HLS_RTL_DIR)/$(BLOCK)/rtl_output --top $(BLOCK) --out $(HLS_RTL_MANIFEST)

hls-elaborate: hls-import
	$(NIX) tren elaborate $(HLS_RTL_MANIFEST)

hls-run: hls-import
	$(NIX) bash -c 'PYTHONPATH=src:$$PYTHONPATH python -m tren.tui --manifest $(HLS_RTL_MANIFEST)'

# --- Vitis HLS example build flow (port from ipyfuse; needs a Xilinx
#      install — run inside `nix develop .#xilinx`) ------------------------

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

## Evaluated on every make invocation. .xilinx_env is gitignored, so it is
## absent in a fresh clone -- swallow the error; an empty value is the
## expected "not configured" state, and %_HLS checks for it.
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