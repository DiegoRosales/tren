# Release process

## Version scheme

TREN uses Semantic Versioning: `MAJOR.MINOR.PATCH`. Git tags add a `v`, for
example `v0.0.1`.

While the major version is 0, a minor release may change the CLI, the key map,
the IR cache layout or another user-visible interface. A patch release contains
fixes only. Version 1.0.0 marks the first compatibility promise for the CLI and
the IR.

## Cadence

There is no calendar schedule. Tag a release when a user-visible feature or a
set of fixes is ready. Release from `main` only. Do not tag a working tree that
has not passed the gates.

## Prepare a release

1. Start from a clean `main` that is level with `origin/main`.

   ```sh
   git switch main
   git status --short
   git pull --ff-only
   ```

2. Set the new version in both files:

   - `pyproject.toml`, `[project].version`
   - `src/tren/__init__.py`, `__version__`

3. Set the README install pins to the new tag. Both commands must carry the
   same tag:

   - `pip install "tren @ git+https://github.com/DiegoRosales/tren@vX.Y.Z"`
   - `git checkout vX.Y.Z` after the source clone

   The source install stays on a tag, not a branch. A tag install is
   reproducible; the default branch is not.

4. Enter the dev shell and run the gates. The full check is always required.

   ```sh
   nix develop
   make check
   ```

   Run `make roundtrip` and `make pane-cue` when the release changes a binding,
   OSC 52, pane styling or a tmux script. Follow the change checklist in
   `docs/verification.md` for other areas.

5. Commit the version bump and the README pins together.

   ```sh
   git add pyproject.toml src/tren/__init__.py README.md
   git commit -m "Release vX.Y.Z"
   ```

6. Create an annotated tag on that commit.

   ```sh
   git tag -a vX.Y.Z -m "TREN vX.Y.Z"
   ```

7. Push the commit and the tag after the release is approved.

   ```sh
   git push origin main
   git push origin vX.Y.Z
   ```

8. Create a GitHub release from the tag. Use the commits since the previous
   tag as the release notes.

## Verify the published install

Use a fresh virtual environment and the exact command from the README:

```sh
python -m venv /tmp/tren-release
. /tmp/tren-release/bin/activate
pip install "tren @ git+https://github.com/DiegoRosales/tren@vX.Y.Z"
python -c 'import importlib.metadata as m; print(m.version("tren"))'
tren --version
```

The metadata command must report the tag without the leading `v`, for example
`0.0.1`. `tren --version` must also report the Python version:

```text
tren 0.0.1 (Python 3.14.8)
```

A source checkout adds `dev-<sha>`:

```text
tren 0.0.1 (dev-abc1234, Python 3.14.8)
```

`tren --version-debug` prints the same first line, then the interpreter,
platform, package path, Git state, dependency versions and configuration
layer paths. Use it in a bug report.

The install needs only the Python dependencies for this check; yosys and
slang are needed to open a design.

## PyPI

TREN is not on PyPI yet. When it is published, replace the Git URL with the
exact PyPI version, for example:

```sh
pip install tren==X.Y.Z
```

Do not use an unpinned install command in the README. A reader must get the
same code on every install.
