"""Theme-derived colours for the hierarchy and trace panes.

The hierarchy pane paints one colour per row kind. A kind is a small
vocabulary, not a widget: the row text and its markers keep their meaning
when the terminal has no colour.

By default each kind follows the active Textual theme. The configuration
file may replace any kind with a colour name or a hex value, or set it to
``none`` to leave that kind uncoloured.
"""

from __future__ import annotations

from typing import Mapping

from textual.color import Color, ColorParseError

THEME = "theme"
NONE = "none"

ROW_KINDS = (
    "scope",
    "module",
    "blackbox",
    "port_in",
    "port_inout",
    "port_out",
    "net",
)

# The hierarchy paints on dark surfaces even when the chrome theme is light,
# so a theme's text-safe role can still be too dark here. Theme values are
# lightened until they clear this ratio on every background they sit on.
MIN_CONTRAST = 4.5

# Textual generates a text-safe variant of each semantic colour. readable()
# adapts it to the fixed dark pane surfaces the app paints on. Each kind uses
# a different role so the rows stay distinguishable.
THEME_ROLES = {
    "scope": "text-primary",
    "module": "foreground-lighten-1",
    "blackbox": "text-warning",
    "port_in": "text-secondary",
    "port_inout": "text-error",
    "port_out": "text-success",
    "net": "foreground-darken-2",
}


def port_kind(direction: str) -> str:
    """Row kind of a port leaf: input, inout or output."""
    if direction == "out":
        return "port_out"
    if direction == "inout":
        return "port_inout"
    return "port_in"


# Trace rows reuse the hierarchy vocabulary by meaning: a boundary crosses
# into a scope, a box is opaque, a cell is the logic inside a module.
TRACE_ROW_KINDS = {
    "boundary": "scope",
    "box": "blackbox",
    "cell": "module",
}


def trace_kind(row: Mapping[str, object] | None) -> str:
    """Row kind of one trace row, or of the traced net when it has no row."""
    if row is None:
        return "net"
    if row.get("unknown"):
        return "blackbox"
    return TRACE_ROW_KINDS.get(str(row.get("kind", "")), "net")


def _luminance(color: Color) -> float:
    channels = []
    for value in (color.r, color.g, color.b):
        channel = value / 255
        channels.append(
            channel / 12.92
            if channel <= 0.03928
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: Color, b: Color) -> float:
    first, second = _luminance(a), _luminance(b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def readable(color: str, backgrounds: tuple[str, ...]) -> str:
    """A theme colour lightened until it clears MIN_CONTRAST.

    The hierarchy surfaces stay dark under every theme, so a dark theme role
    (for example a light theme's ``text-primary``) must not be used as is.
    An unparseable colour is returned unchanged.
    """
    if not backgrounds:
        return color
    try:
        parsed = Color.parse(color)
        plates = [Color.parse(background) for background in backgrounds]
    except ColorParseError:
        return color
    if all(_contrast(parsed, plate) >= MIN_CONTRAST for plate in plates):
        return parsed.hex6
    for step in range(1, 21):
        candidate = parsed.lighten(step / 20)
        if all(_contrast(candidate, plate) >= MIN_CONTRAST for plate in plates):
            return candidate.hex6
    return "#FFFFFF"


def resolve_row_colors(
    variables: Mapping[str, str],
    *,
    scope: str = THEME,
    module: str = THEME,
    blackbox: str = THEME,
    port: str = THEME,
    inout: str = THEME,
    net: str = THEME,
    backgrounds: tuple[str, ...] = (),
) -> dict[str, str]:
    """Resolve the configured row colours to concrete values.

    ``variables`` is the active theme's generated colour system. ``theme``
    takes the role for that kind and lightens it for ``backgrounds``, a
    colour name or hex value is used as written, and ``none`` leaves the row
    uncoloured. The ``port`` setting covers input and output ports; ``inout``
    covers bidirectional ports. ``theme`` gives each direction its own role.
    """
    configured = {
        "scope": scope,
        "module": module,
        "blackbox": blackbox,
        "port_in": port,
        "port_inout": inout,
        "port_out": port,
        "net": net,
    }
    colors: dict[str, str] = {}
    for kind in ROW_KINDS:
        value = configured[kind]
        if value == THEME:
            role = variables.get(THEME_ROLES[kind], "")
            colors[kind] = readable(role, backgrounds) if role else ""
        elif value == NONE:
            colors[kind] = ""
        else:
            colors[kind] = value
    return colors


def row_style(
    kind: str, colors: Mapping[str, str], *, bold: bool = False
) -> str:
    """Rich style for one row kind, or an empty string when uncoloured."""
    color = colors.get(kind, "")
    if bold and color:
        return f"bold {color}"
    if bold:
        return "bold"
    return color
