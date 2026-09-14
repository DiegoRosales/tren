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
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          name = "tren";

          packages =
            with pkgs;
            [
              # UI framework (pinned: textual 8.2.8 — see README)
              (python3.withPackages (ps: [
                ps.textual
              ]))

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
    };
}