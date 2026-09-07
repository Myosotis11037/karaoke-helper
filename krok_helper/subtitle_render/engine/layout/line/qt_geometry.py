"""Qt text measurement adapter for backend-independent line geometry policy."""

from __future__ import annotations

from PyQt6.QtGui import QFontMetrics

from krok_helper.subtitle_render.engine.guide import vector_glyph_width
from krok_helper.subtitle_render.engine.layout.line.geometry import (
    line_has_role_labels,
    resolve_char_intervals,
)
from krok_helper.subtitle_render.engine.text import (
    build_role_text_layout,
    role_char_geometry_by_index,
)
from krok_helper.subtitle_render.engine.text import (
    build_font,
    build_latin_font,
    char_layout_width,
    make_font_for,
)
from krok_helper.subtitle_render.domain.models import Style
from krok_helper.subtitle_render.domain.timing import TimingLine


def char_widths_for_intervals(
    line: TimingLine,
    style: Style,
) -> list[int]:
    """Measure interval weights for normal, role-styled and guide glyphs."""
    if line_has_role_labels(line):
        layout = build_role_text_layout(line, style, x0=0, baseline_y=0)
        widths, _ranges = role_char_geometry_by_index(line, layout)
        return widths
    font = build_font(style)
    metrics = QFontMetrics(font)
    latin_font = build_latin_font(style)
    font_for = make_font_for(style, font, latin_font)
    latin_metrics = QFontMetrics(latin_font) if font_for is not None else metrics
    return [
        (
            vector_glyph_width(char.vector_glyph, style)
            if char.vector_glyph is not None
            else char_layout_width(
                char.text,
                font,
                metrics,
                latin_metrics,
                font_for,
                style,
            )
        )
        for char in line.chars
    ]


def resolved_char_intervals_for_line(
    line: TimingLine,
    style: Style,
) -> list[tuple[int, int]]:
    """Resolve final Painter-compatible character timing intervals."""
    return resolve_char_intervals(line, style, char_widths_for_intervals)


__all__ = [
    "char_widths_for_intervals",
    "resolved_char_intervals_for_line",
]
