{
  description = "TREN - TUI RTL explorer implementation shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Upstream source builds of yosys, used by the #tren-yosys-src shell.
    # The flake pins its own self.submodules; requires Nix >= 2.27. The
    # github URL scheme rejects a submodule attribute, so fetch through git.
    yosys = {
      url = "git+https://github.com/YosysHQ/yosys";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { self, nixpkgs, pyproject-nix, yosys, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # The #xilinx FHS sandbox is x86_64-linux only (Xilinx tools ship for no
      # other platform) and imports its own pkgs with a small overlay.
      system = "x86_64-linux";
      lz4Overlay = final: prev: {
        lz4 = prev.lz4.overrideAttrs (old: {
          postInstall = (old.postInstall or "") + ''
            ln -s $out/bin/lz4 $out/bin/lz4c
          '';
        });
      };
      xilinxPkgs =
        import nixpkgs {
          inherit system;
          overlays = [ lz4Overlay ];
        };
      ccSalt = xilinxPkgs.stdenv.cc.suffixSalt;

      # Pin the host/native compiler to GCC 14. nixpkgs-unstable now ships
      # GCC 15, which defaults to C23 where `bool`/`true`/`false` are keywords;
      # that breaks older-style build-host helper tools (e.g. ghostscript's
      # `typedef int bool;`). wrapCCMulti keeps multilib and the cc-wrapper
      # glibc/crt injection that host-tool builds rely on. (Port from ipyfuse.)
      gccMulti14 = xilinxPkgs.wrapCCMulti xilinxPkgs.gcc14;

      # TREN's Python env (textual UI), for the shells that put tren on
      # PYTHONPATH rather than installing it.
      trenPython = pkgs: pkgs.python3.withPackages (ps: [ ps.textual ]);

      # PEP 621 view of the project. Dependencies are declared once, in
      # pyproject.toml, and consumed two ways: by buildPythonPackage below,
      # and by pip in the venv shell. The three dev shells differ in *how*
      # tren reaches the interpreter, never in what it depends on.
      pyproject = pyproject-nix.lib.project.loadPyproject { projectRoot = ./.; };

      # tren built as a store path, dependencies resolved the nix way. The
      # console scripts get yosys and slang on PATH, so `nix run` and
      # `nix shell` work without entering a dev shell.
      trenPackage =
        pkgs:
        pkgs.python3.pkgs.buildPythonPackage (
          (pyproject.renderers.buildPythonPackage { python = pkgs.python3; })
          // {
            makeWrapperArgs = [
              "--prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.yosys pkgs.sv-lang ]}"
            ];
          }
        );

      # An interpreter with tren *installed*: `import tren` and the `tren` /
      # `tren-tui` console scripts resolve into the store, exactly as they
      # would for someone who installed the package - no PYTHONPATH, no venv,
      # and no dependence on the directory you happen to be standing in.
      trenEnv =
        pkgs:
        pkgs.python3.withPackages (
          pyproject.renderers.withPackages {
            python = pkgs.python3;
            extraPackages = _ps: [ (trenPackage pkgs) ];
          }
        );

      # yosys built from upstream source via the YosysHQ flake, for the
      # #tren-yosys-src shell. It tracks the revision pinned in flake.lock;
      # bump with `nix flake lock --update-input yosys`. `yosys-gcc` keeps
      # the same compiler as the nixpkgs build, so a behaviour difference
      # is the source's, not the toolchain's.
      yosysSrc = sys: yosys.packages.${sys}.yosys-gcc;

      # Everything TREN shells out to except yosys. The default / tren /
      # tren-venv shells add nixpkgs' packaged yosys; #tren-yosys-src adds
      # the source build above. Sharing the rest keeps the shells from
      # drifting apart on tool versions - the thing every gate assumes.
      trenToolsWithoutYosys =
        pkgs: with pkgs; [
          # Elaboration backends TREN shells out to
          sv-lang # slang / slang-hier / slang --ast-json scope tree + params
          verilator # optional: manifest importer test subject, -E/--json-only

          # Earmarked rewrite target
          rustc
          cargo

          gnumake # the Makefile runs inside the shell, so `make` must be here
          jq
          ripgrep
          tmux # real-terminal OSC 52 round-trip (tools/osc52_roundtrip.sh)
        ];

      # Everything TREN shells out to. Shared by all three shells so they
      # cannot drift apart on tool versions - the thing every gate assumes.
      # `pkgs.yosys` is spelled out: the `yosys` flake input shadows the
      # `with pkgs` name, and receives a flake set, not a derivation.
      trenTools =
        pkgs: [
          pkgs.yosys # write_json netlist (proc, never opt)
        ] ++ trenToolsWithoutYosys pkgs;

      # Shell banner. `where` names the shell and says how tren got here, so a
      # stray `nix develop` in the wrong shell is visible in one line.
      trenBanner = where: ''
        echo
        echo "  TREN dev shell - ${where}"
        echo "  --------------"
        printf '  %-10s %s\n' yosys    "$(yosys -V 2>/dev/null | head -1)"
        printf '  %-10s %s\n' slang    "$(slang --version 2>/dev/null | head -1)"
        printf '  %-10s %s\n' rustc    "$(rustc --version 2>/dev/null)"
        printf '  %-10s %s\n' textual  "$(python3 -c "import textual; print('textual', textual.__version__)" 2>/dev/null || echo n/a)"
        printf '  %-10s %s\n' tren     "$(command -v tren 2>/dev/null || echo 'n/a (python -m tren)')"
        echo
        echo "  Rules from AGENTS.md: yosys JSON needs proc; never opt; don't flatten;"
        echo "  cross-scope bit matching is positional-only."
        echo
      '';

      # `tren` and `tren-tui` for the shells that do not install the package:
      # both run the repo's own src/ through PYTHONPATH, so edits take effect
      # with no reinstall, and both console scripts exist in every shell. Use
      # the textual-carrying interpreter: the bare/open command imports
      # tren.tui, which needs textual and rich. Plain pkgs.python3 has neither.
      trenFromSource =
        pkgs:
        let
          script = name: module: pkgs.writeShellScriptBin name ''
            set -eu
            SRC="''${TREN_SRC:-$PWD/src}"
            export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}$SRC"
            exec ${trenPython pkgs}/bin/python -m ${module} "$@"
          '';
        in
        pkgs.symlinkJoin {
          name = "tren-from-source";
          paths = [
            (script "tren" "tren")
            (script "tren-tui" "tren.tui")
          ];
        };
    in
    {
      # `nix build` / `nix run .#tren` - the same store path .#tren shells in.
      packages = forAllSystems (pkgs: rec {
        tren = trenPackage pkgs;
        default = tren;
      });

      devShells =
        let
          base = forAllSystems (pkgs: {

            # Default: the working shell. tren is *not* installed - the `tren`
            # wrapper and every `make` target read the checkout's src/ through
            # PYTHONPATH, so an edit is live on the next command. This is the
            # shell all the gates in docs/verification.md assume.
            # Usage: nix develop
            default = pkgs.mkShell {
              name = "tren";
              packages = [
                # UI framework (pinned: textual 8.2.8 - see README)
                (trenPython pkgs)
                (trenFromSource pkgs)
              ] ++ trenTools pkgs;
              shellHook = trenBanner "default (src/ on PYTHONPATH)";
            };

            # tren packaged the nix way: buildPythonPackage builds the whole
            # project into the store with its declared dependencies, so
            # `import tren` and the `tren` / `tren-tui` console scripts behave
            # exactly as they would for a consumer of the package. Nothing
            # here reads the checkout, which is the point - it is how you
            # catch a module that only ever worked because src/ was on the
            # path, or a dependency the code uses but pyproject.toml forgot.
            # Edits need a re-entry (or `nix develop .#tren --refresh`).
            # Usage: nix develop .#tren
            tren = pkgs.mkShell {
              name = "tren-pkg";
              packages = [ (trenEnv pkgs) ] ++ trenTools pkgs;
              shellHook = trenBanner "#tren (installed from the nix store)";
            };

            # The venv counterpart of .#tren. Instead of building tren and
            # resolving its dependencies through nix, this shell creates a
            # plain Python venv on first entry, pip-installs the project
            # *editable* from the current directory, and activates it - the
            # textbook Python flow, on top of nix's interpreter and backends.
            #
            # Enter it from the project root: the hook installs from `.` and
            # creates `.venv/` in the working directory, so the editable
            # install points back at your checkout and edits show up
            # immediately. First entry needs network (pip fetches textual and
            # rich from PyPI); later entries just activate. Reset with
            # `rm -rf .venv/` and re-enter. `TREN_VENV_DIR` overrides `.venv`.
            # Usage: nix develop .#tren-venv
            tren-venv = pkgs.mkShell {
              name = "tren-venv";
              packages = [ pkgs.python3 ] ++ trenTools pkgs;
              shellHook = ''
                if [ ! -f pyproject.toml ]; then
                  echo "error: no pyproject.toml in $(pwd)." >&2
                  echo "  Enter this shell from the project root, e.g.:" >&2
                  echo "  cd /path/to/tren && nix develop .#tren-venv" >&2
                  echo "  (the hook runs 'pip install -e .' in the current directory)" >&2
                  exit 1
                fi
                export VENV_DIR="''${TREN_VENV_DIR:-.venv}"
                if [ ! -d "$VENV_DIR" ]; then
                  echo "Creating venv at $VENV_DIR and installing tren (-e) ..."
                  python -m venv "$VENV_DIR"
                  "$VENV_DIR/bin/pip" install -e .
                fi
                # shellcheck disable=SC1091
                . "$VENV_DIR/bin/activate"
              ''
              + trenBanner "#tren-venv (editable install in .venv)";
            };

            # `default` with yosys built from upstream source through the
            # YosysHQ flake instead of nixpkgs' packaged release. For
            # verifying tren against yosys main. The first entry fetches the
            # source and its submodules and builds the whole tool, so allow
            # the time. Refresh the source with `nix flake lock
            # --update-input yosys`, then re-verify layer 2 of
            # docs/verification.md.
            # Usage: nix develop .#tren-yosys-src
            tren-yosys-src = pkgs.mkShell {
              name = "tren-yosys-src";
              packages = [
                (trenPython pkgs)
                (trenFromSource pkgs)
                (yosysSrc pkgs.system)
              ] ++ trenToolsWithoutYosys pkgs;
              shellHook = trenBanner "#tren-yosys-src (yosys built from YosysHQ source)";
            };
          });
        in
        base
        // {
          ${system} =
            base.${system}
            // {
              # FHS sandbox for running Xilinx tools (Vivado, Vitis HLS, ...).
              # Xilinx installers and binaries expect a conventional /usr/lib
              # layout that NixOS does not provide natively; buildFHSEnv
              # creates that layout. Ported from ipyfuse's flake.
              # Usage: nix develop .#xilinx
              xilinx =
                (xilinxPkgs.buildFHSEnv {
                  name = "xilinx-env";
                  targetPkgs = pkgs: with pkgs; [
                    ncurses5
                    ncurses
                    libxcrypt-legacy
                    libpng
                    libusb1
                    systemd
                    pixman
                    zlib
                    libuuid
                    bash
                    coreutils
                    xorg.libXext
                    xorg.libX11
                    xorg.libXrender
                    xorg.libXtst
                    xorg.libXi
                    xorg.libXft
                    xorg.libxcb
                    freetype
                    fontconfig
                    glib
                    gtk2
                    gtk3
                    graphviz
                    # Use the *wrapped* toolchain (gcc_multi and binutils are
                    # cc-wrapper / binutils-wrapper scripts). The wrappers inject the
                    # -B<glibc>/lib, crt paths (Scrt1.o, crti.o), -lgcc_s and
                    # dynamic-linker flags by reading the NIX_* env vars set in
                    # `profile`. The raw, unwrapped compiler (stdenv.cc.cc) ignores
                    # those vars and cannot locate the Nix-store glibc startup files,
                    # which breaks host-tool builds like u-boot's fixdep. stdenv.cc.cc
                    # is deliberately NOT listed so it doesn't shadow the wrapper at
                    # /usr/bin/gcc; stdenv.cc.cc.lib (runtime libs only) is kept.
                    gccMulti14
                    binutils
                    glibc
                    glibc.dev
                    stdenv.cc.cc.lib
                    zstd
                    unzip
                    nettools
                    verilator
                    (trenPython pkgs)
                    gitRepo
                    chrpath
                    diffstat
                    lz4
                    rpcsvc-proto
                    parted
                    git
                    gnumake # the HLS Makefile flow runs inside the sandbox
                    clang-tools
                    openssl
                    curl
                    perl
                    e2fsprogs
                    mtools
                    dosfstools
                    # Extra stuff for Vitis IDE
                    # For future reference, when it silently fails to launch, run:
                    # RDI_VERBOSE=True vitis
                    nss
                    nspr
                    dbus
                    atk
                    cups
                    libdrm
                    pango
                    cairo
                    libxcomposite
                    libxdamage
                    libxfixes
                    libxrandr
                    libgbm
                    expat
                    libxkbcommon
                    libxkbfile
                    alsa-lib
                    libGL

                    # TREN tooling, so the freshly exported rtl_output can be
                    # imported/elaborated without leaving the sandbox
                    yosys
                    sv-lang
                    tmux
                    (trenFromSource pkgs)
                  ];
                  profile = ''
                    export LD_LIBRARY_PATH=/usr/lib:/usr/lib64:$LD_LIBRARY_PATH

                    # BitBake strips unknown env vars before forking subprocesses.
                    # The NixOS gcc wrapper needs these to locate glibc startup files
                    # (Scrt1.o, crti.o) and libgcc_s in the Nix store - without them
                    # host-tool compilation (e.g. u-boot fixdep) fails.
                    export NIX_DONT_SET_RPATH_${ccSalt}=1
                    export NIX_DYNAMIC_LINKER_${ccSalt}=/lib/ld-linux-x86-64.so.2
                    export BB_ENV_PASSTHROUGH_ADDITIONS="NIX_LDFLAGS NIX_CFLAGS_COMPILE NIX_CFLAGS_LINK NIX_CC_WRAPPER_TARGET_HOST_${ccSalt} NIX_DONT_SET_RPATH_${ccSalt} NIX_DYNAMIC_LINKER_${ccSalt}"
                  '';
                  runScript = ''
                    env LIBRARY_PATH=/usr/lib \
                        CMAKE_LIBRARY_PATH=/usr/lib \
                        CMAKE_INCLUDE_PATH=/usr/include \
                        bash
                  '';
                }).env;
            };
        };
    };
}