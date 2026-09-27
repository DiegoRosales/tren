"""Unit tests for the shared key map (deck card 248).

The `?` cheatsheet and the README "Keys" table render from one declarative
list, so the two cannot drift. These tests pin the list contents (what the
cheatsheet must show) and guard the committed README block: regenerate it with
`nix develop . -c python tools/guide.py` (or `make guide`) after a binding
change.

Run:  nix develop . -c bash -c "PYTHONPATH=src python -m unittest discover -s tests"
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tren.keys import (  # noqa: E402
    KEYS_BEGIN,
    KEYS_END,
    SCREEN_KEYS,
    TRACE_KEYS,
    render_keys_table,
    render_readme_block,
    render_trace_paragraph,
)

README = Path(__file__).resolve().parents[1] / "README.md"


class KeyMapContentsTests(unittest.TestCase):
    """The list is what the cheatsheet must show (acceptance: yank + editor)."""

    def test_cheatsheet_lists_yank_and_editor_keys(self) -> None:
        effects = " ".join(doc.effect for doc in SCREEN_KEYS)
        self.assertIn("yank the selection", effects)
        self.assertIn("$EDITOR", effects)

    def test_the_full_screen_key_map_is_present(self) -> None:
        by_keys = {doc.keys: doc for doc in SCREEN_KEYS}
        for keys in (
            "1 2 3",
            "z",
            "j k",
            "l",
            "h",
            "arrows",
            "enter",
            "ctrl+u",
            "y",
            "Y",
            "d L",
            "X",
            ";",
            "e",
            "E",
            "?",
            "q",
        ):
            self.assertIn(keys, by_keys, f"key map missing {keys!r}")

    def test_key_and_effect_are_never_empty(self) -> None:
        for doc in (*SCREEN_KEYS, *TRACE_KEYS):
            self.assertTrue(doc.keys.strip(), f"empty keys in {doc}")
            self.assertTrue(doc.effect.strip(), f"empty effect in {doc}")

    def test_trace_keys_cover_the_modal_bindings(self) -> None:
        self.assertTrue(any(doc.keys == "enter or right" for doc in TRACE_KEYS))
        self.assertTrue(any(doc.keys == "backspace or left" for doc in TRACE_KEYS))
        self.assertTrue(any(doc.keys == "down/up" for doc in TRACE_KEYS))
        self.assertTrue(any(doc.keys == "escape" for doc in TRACE_KEYS))


class ReadmeSyncTests(unittest.TestCase):
    """The committed README block must equal what the key map renders."""

    def test_the_readme_block_matches_the_key_map(self) -> None:
        text = README.read_text()
        start = text.index(KEYS_BEGIN)
        end = text.index(KEYS_END) + len(KEYS_END)
        self.assertEqual(text[start:end], render_readme_block())

    def test_the_readme_mentions_the_cheatsheet_key(self) -> None:
        text = README.read_text()
        self.assertIn("`?`", text)
        self.assertIn("cheatsheet", text)


class RendererTests(unittest.TestCase):
    def test_table_body(self) -> None:
        table = render_keys_table()
        self.assertTrue(table.startswith("| Key | Effect |\n"))
        self.assertIn("| `y` | yank the selection", table)

    def test_trace_paragraph(self) -> None:
        paragraph = render_trace_paragraph()
        self.assertTrue(paragraph.startswith("In the **trace modal**: "))
        self.assertIn("`enter` or `right`", paragraph)
        self.assertIn("`escape` closes it.", paragraph)


if __name__ == "__main__":
    unittest.main()