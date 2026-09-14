{
  description = "TREN — TUI RTL explorer implementation shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
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

      # TREN's Python env (textual UI), shared by both shells.
      trenPython = pkgs: pkgs.python3.withPackages (ps: [ ps.textual ]);
    in
    {
      devShells =
        let
          base = forAllSystems (pkgs: {
            default = pkgs.mkShell {
              name = "tren";

              packages =
                with pkgs;
                [
                  # UI framework (pinned: textual 8.2.8 — see README)
                  (trenPython pkgs)

                  # Elaboration backends TREN shells out to
                  yosys # write_json netlist (proc, never opt)
                  sv-lang # slang / slang-hier / slang --ast-json scope tree + params
                  verilator # optional: manifest importer test subject, -E/--json-only

                  # Earmarked rewrite target
                  rustc
                  cargo

                  jq
                  ripgrep
                  tmux # real-terminal OSC 52 round-trip (card 214 acceptance, tools/osc52_roundtrip.sh)

                  # tren CLI entry point (python -m tren; PYTHONPATH resolves to
                  # the repo's src/ from the invocation directory, overridable)
                  (pkgs.writeShellScriptBin "tren" (
                    ''
                      set -eu
                      SRC="''${TREN_SRC:-$PWD/src}"
                      export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}$SRC"
                      exec ${pkgs.python3}/bin/python -m tren "$@"
                    ''
                  ))
                ];

              shellHook = ''
                echo
                echo "  TREN dev shell"
                echo "  --------------"
                printf '  %-10s %s\n' yosys    "$(yosys -V 2>/dev/null | head -1)"
                printf '  %-10s %s\n' slang    "$(slang --version 2>/dev/null | head -1)"
                printf '  %-10s %s\n' rustc    "$(rustc --version 2>/dev/null)"
                printf '  %-10s %s\n' textual  "$(python3 -c "import textual; print('textual', textual.__version__)" 2>/dev/null || echo n/a)"
                echo
                echo "  Rules from AGENTS.md: yosys JSON needs proc; never opt; don't flatten;"
                echo "  cross-scope bit matching is positional-only."
                echo
              '';
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
                    (pkgs.writeShellScriptBin "tren" (
                      ''
                        set -eu
                        SRC="''${TREN_SRC:-$PWD/src}"
                        export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}$SRC"
                        exec ${pkgs.python3}/bin/python -m tren "$@"
                      ''
                    ))
                  ];
                  profile = ''
                    export LD_LIBRARY_PATH=/usr/lib:/usr/lib64:$LD_LIBRARY_PATH

                    # BitBake strips unknown env vars before forking subprocesses.
                    # The NixOS gcc wrapper needs these to locate glibc startup files
                    # (Scrt1.o, crti.o) and libgcc_s in the Nix store — without them
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