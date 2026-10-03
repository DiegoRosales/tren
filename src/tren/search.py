"""Pure search index + fuzzy ranker for the TUI (deck card 244).

One built-in finder scans every searchable item: modules (scope paths),
instances, ports, nets and files (source files carrying a scope's src).
The index is an eager O(scopes) walk of the in-memory IR -- the "lazy"
rule in AGENTS.md applies to the Tree widget's materialisation, not to the
IR, so this is free. The ranker is a classic subsequence fuzzy scorer
(fzf-style): a contiguous run scores higher than the same letters spread
out, and a match at a boundary (`.`, `/`, `_`, camel hump) scores higher
still.

A keystroke must not rescan every path in Python, or the finder stalls on a
large design. Three things keep it fast: `build_search_index` returns a
`SearchIndex` that carries each path casefolded once; `filter_and_rank`
rejects a non-match with a C-speed `str.find` subsequence scan before the
scorer runs; and the scorer walks the query's characters only (the text
positions where one of them matches), never a full query-by-text matrix.
The result set and the scores are identical to scoring every entry in full,
so the ordering pins stay intact.

Everything here is plain Python and framework-agnostic, so a ratatui
rewrite keeps it unchanged (AGENTS.md section 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .ir import Design, Instance, Scope, parse_src

# Result ordering within one score: modules first, then instances, ports,
# nets, files last.
KIND_ORDER = {"module": 0, "instance": 1, "port": 2, "net": 3, "file": 4}

# Sentinel below any real score. Scores are small integers (contiguous-run
# bonuses sum to a few times the query length), so this is safely far below.
_NEG = -(1 << 30)


@dataclass(frozen=True)
class SearchEntry:
    """One row the finder can return and jump to."""

    kind: str  # module | instance | port | net | file
    path: str  # human/search text, e.g. top.u_core.alu_result
    name: str  # bare name (alu_result)
    scope: Scope | None = None  # the module scope the item belongs to
    instance: Instance | None = None  # set for kind == instance
    src: str | None = None  # raw source span for jump/preview


class SearchIndex(list[SearchEntry]):
    """The searchable rows with their casefolded paths precomputed.

    `build_search_index` returns one of these; `filter_and_rank` reads the
    prefolded paths so a keystroke never re-folds every path. It is a plain
    `list` subclass on purpose: tests and a ratatui importer can treat it
    like the list it replaces.
    """

    def __init__(self, entries: Iterable[SearchEntry]) -> None:
        super().__init__(entries)
        self.folded: tuple[str, ...] = tuple(e.path.casefold() for e in entries)


def _remember(
    files: dict[str, tuple[str | None, Scope | None]],
    src: str | None,
    container: Scope | None,
) -> None:
    """First src naming a new file becomes that file entry's representative."""
    parsed = parse_src(src)
    if parsed is not None and parsed[0] not in files:
        files[parsed[0]] = (src, container)


def build_search_index(design: Design) -> SearchIndex:
    """Every searchable item of the design, in a stable order."""
    entries: list[SearchEntry] = []
    files: dict[str, tuple[str | None, Scope | None]] = {}
    for scope in design.scopes:
        entries.append(
            SearchEntry("module", scope.name, scope.name, scope=scope, src=scope.src)
        )
        _remember(files, scope.src, scope)
        for inst in scope.instances:
            path = f"{scope.name}.{inst.name}"
            entries.append(
                SearchEntry("instance", path, inst.name, scope=scope, instance=inst, src=inst.src)
            )
            _remember(files, inst.src, scope)
        for port in scope.ports:
            path = f"{scope.name}.{port.name}"
            entries.append(SearchEntry("port", path, port.name, scope=scope, src=port.src))
            _remember(files, port.src, scope)
        for net in scope.nets:
            path = f"{scope.name}.{net.name}"
            entries.append(SearchEntry("net", path, net.name, scope=scope, src=net.src))
            _remember(files, net.src, scope)
    for file, (src, container) in files.items():
        entries.append(SearchEntry("file", file, file, scope=container, src=src))
    return SearchIndex(entries)


def _boundary(text: str, index: int) -> bool:
    """The match at text[index] starts a segment: after a separator or a
    camel hump. A '_' before a lowercase segment needs no lookahead: the
    separator itself is the boundary, which already covers u_core -> u."""
    if index > 0 and not text[index - 1].isalnum():
        return True
    if index > 0 and text[index - 1].islower() and text[index].isupper():
        return True
    return False


def _has_subsequence(text: str, query: str) -> bool:
    """text holds the query's characters in order (a C-speed check).

    `str.find` scans in C, so rejecting a non-match costs one scan of
    `text` per query character at worst and almost always far less: the
    first absent character returns early.
    """
    pos = 0
    for ch in query:
        pos = text.find(ch, pos)
        if pos < 0:
            return False
        pos += 1
    return True


def _score_folded(query: str, text: str) -> int | None:
    """Score a pair of already-casefolded strings; see `fuzzy_score`.

    A left-to-right subsequence DP, but the inner loop walks the query's
    characters in the order they appear in `text` (via a char -> positions
    map), not `range(n)` per text position. The lazy `ended_at` stamping
    replaces a fresh per-position array. This reproduces the naive DP
    exactly:

    - `ended[i]` feeds the +2 run bonus only when its last match sat at the
      previous text position (`ended_at[i] == j - 1`).
    - The `ended` row's index 0 carries a live 0 only for the first text
      position (`j == 0`), which is what the original's initialisation
      `ended[0] = 0` followed by `new_ended[0] = neg` per position produces.
    - The indices whose query char equals `text[j]` are visited in
      *descending* order, so a match at index i never reads the value
      index i-1 may have just written for the *same* text position: the
      original computes a whole fresh ended row per position from the
      previous position's row. `ended[i]` is then overwritten, never maxed
      against its stale predecessor - each (i, j) pair is visited at most
      once, and `best[i]` already accumulates the maximum across positions.
    """
    if not query:
        return 0
    if not text:
        return None
    n = len(query)
    if n == 1:
        ch = query
        best = _NEG
        pos = -1
        while True:
            pos = text.find(ch, pos + 1)
            if pos < 0:
                break
            base = 2 if pos == 0 else 0
            val = base + 3 + (1 if _boundary(text, pos) else 0)
            if val > best:
                best = val
        return None if best == _NEG else best
    char_pos: dict[str, list[int]] = {}
    for i, ch in enumerate(query, 1):
        char_pos.setdefault(ch, []).append(i)
    best = [_NEG] * (n + 1)
    ended = [_NEG] * (n + 1)
    ended_at = [_NEG] * (n + 1)
    best[0] = ended[0] = 0
    ended_at[0] = -1  # the j == 0 only run bonus (see `_score_folded` notes)
    for j, ch in enumerate(text):
        positions = char_pos.get(ch)
        if positions is None:
            continue
        prev_j = j - 1
        for i in reversed(positions):
            if ended_at[i - 1] == prev_j:
                base = ended[i - 1] + 2
            elif best[i - 1] > _NEG:
                base = best[i - 1]
            else:
                continue
            val = base + 3 + (1 if _boundary(text, j) else 0)
            if val > best[i]:
                best[i] = val
            ended[i] = val
            ended_at[i] = j
    score = best[n]
    return None if score == _NEG else score


def fuzzy_score(query: str, text: str) -> int | None:
    """Score how well query matches text as a subsequence, or None.

    Case-insensitive. A higher score is better: +3 per matched character,
    +2 when it continues a run started at the previous text position,
    +1 when it lands on a segment boundary. The empty query matches
    everything with score 0.
    """
    return _score_folded(query.casefold(), text.casefold())


def filter_and_rank(
    entries: SearchIndex | list[SearchEntry], query: str
) -> list[SearchEntry]:
    """Entries matching the query, best fuzzy score first.

    The empty query returns every entry in index order (grouped by kind),
    which is what the finder shows before anything is typed. The result set
    and the per-entry scores equal scoring every entry with `fuzzy_score`
    in full; the subsequence prefilter and the position-walk scorer only
    run faster, never differently.
    """
    q = query.strip()
    if not q:
        return sorted(entries, key=lambda e: (KIND_ORDER[e.kind], e.path))
    qf = q.casefold()
    folded = entries.folded if isinstance(entries, SearchIndex) else (
        e.path.casefold() for e in entries
    )
    hits: list[tuple[int, SearchEntry]] = []
    for entry, text in zip(entries, folded):
        if not _has_subsequence(text, qf):
            continue
        score = _score_folded(qf, text)
        if score is not None:
            hits.append((score, entry))
    hits.sort(key=lambda pair: (-pair[0], KIND_ORDER[pair[1].kind], pair[1].path))
    return [e for _, e in hits]