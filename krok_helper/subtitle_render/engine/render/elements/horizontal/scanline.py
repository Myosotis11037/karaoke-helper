"""Karaoke scan-line highlight painted at the moving wipe front."""

from __future__ import annotations

from dataclasses import dataclass, replace

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QTransform

from krok_helper.subtitle_render.domain.models import Style
from krok_helper.subtitle_render.domain.paint import (
    KaraokeColors,
    KaraokeColorState,
    PaintFill,
)
from krok_helper.subtitle_render.engine.render.elements.horizontal.wipe import (
    fill_clip_band,
    segment_fill_ratio,
)
from krok_helper.subtitle_render.engine.render.effects import (
    main_stroke2_width,
    paint_fill_path,
    paint_stroke_path,
    stroke2_pen_width,
    stroke_pen_width,
)


@dataclass(frozen=True)
class ScanlineParams:
    """Resolved scan-line visual parameters shared by main text and ruby."""

    width_px: float
    color: str
    glow_px: int
    mode: str = "color"
    brightness: float = 0.6

    @property
    def half_width(self) -> float:
        return max(self.width_px, 1.0) / 2.0

    @property
    def is_brighten(self) -> bool:
        return self.mode == "brighten"

    @property
    def solid_half_width(self) -> float:
        """Hard core left after the requested inner feather is removed."""

        return max(self.half_width - float(self.glow_px), 0.0)


def scanline_params_for_style(style: Style) -> ScanlineParams:
    """Return the scan-line parameters carried by one style."""

    mode = (
        style.scanline_mode if style.scanline_mode in {"color", "brighten"} else "color"
    )
    return ScanlineParams(
        width_px=max(float(style.scanline_width_px), 1.0),
        color=style.scanline_color or "#FFFFFF",
        glow_px=max(int(style.scanline_glow_px), 0),
        mode=mode,
        brightness=min(max(int(style.scanline_brightness_pct), 0), 100) / 100.0,
    )


def scanline_band_rect(
    rect: QRectF,
    front: float,
    params: ScanlineParams,
    *,
    vertical: bool,
) -> QRectF:
    """Return the highlight band centred on the wipe front."""

    half = params.half_width
    if vertical:
        return QRectF(
            rect.left(),
            front - half,
            max(rect.width(), 1.0),
            half * 2.0,
        )
    return QRectF(
        front - half,
        rect.top(),
        half * 2.0,
        max(rect.height(), 1.0),
    )


def band_touches_rect(band: QRectF, rect: QRectF, pad: float) -> bool:
    return band.intersects(
        QRectF(
            rect.left() - pad,
            rect.top() - pad,
            rect.width() + pad * 2.0,
            rect.height() + pad * 2.0,
        )
    )


def scanline_feather_slices(
    rect: QRectF,
    front: float,
    params: ScanlineParams,
    *,
    vertical: bool,
) -> list[tuple[QRectF, float]]:
    """Approximate an inner-only soft band with non-overlapping alpha slices."""

    half = params.half_width
    softness = min(max(float(params.glow_px), 0.0), half)
    core = half - softness
    count = max(1, min(int(round(half * 2.0)), 64))
    step = half * 2.0 / count
    result: list[tuple[QRectF, float]] = []
    for index in range(count):
        offset = -half + index * step
        distance = abs(offset + step * 0.5)
        if softness <= 0.0 or distance <= core:
            alpha = 1.0
        else:
            progress = max(0.0, min((half - distance) / softness, 1.0))
            alpha = progress * progress * (3.0 - 2.0 * progress)
        if alpha <= 0.0:
            continue
        slice_rect = (
            QRectF(rect.left(), front + offset, rect.width(), step)
            if vertical
            else QRectF(front + offset, rect.top(), step, rect.height())
        )
        result.append((slice_rect, alpha))
    return result


def _solid_scanline_fill(color: str) -> PaintFill:
    return PaintFill(
        mode="solid",
        color=color,
        start_color=color,
        end_color=color,
        gradient_stops=[(0, color), (100, color)],
        split_top_color=color,
        split_bottom_color=color,
        split_stops=[(0, color), (100, color)],
    )


def _brighten_color_hsv(value: str, amount: float) -> str:
    """Raise HSV value without changing the source hue or saturation."""

    color = QColor(value)
    if not color.isValid():
        return value
    hue, saturation, brightness, alpha = color.getHsvF()
    brightness = brightness + (1.0 - brightness) * max(0.0, min(amount, 1.0))
    result = QColor()
    result.setHsvF(hue, saturation, brightness, alpha)
    return result.name(
        QColor.NameFormat.HexArgb if alpha < 1.0 else QColor.NameFormat.HexRgb
    )


def _brighten_fill_hsv(fill: PaintFill, amount: float) -> PaintFill:
    """Return a fill whose authored colours have a higher HSV value."""

    brighten = lambda value: _brighten_color_hsv(value, amount)
    return replace(
        fill,
        color=brighten(fill.color),
        start_color=brighten(fill.start_color),
        end_color=brighten(fill.end_color),
        gradient_stops=[
            (position, brighten(color)) for position, color in fill.gradient_stops
        ],
        split_top_color=brighten(fill.split_top_color),
        split_bottom_color=brighten(fill.split_bottom_color),
        split_stops=[
            (position, brighten(color)) for position, color in fill.split_stops
        ],
    )


def _brighten_state_hsv(state: KaraokeColorState, amount: float) -> KaraokeColorState:
    return KaraokeColorState(
        text=_brighten_fill_hsv(state.text, amount),
        stroke=_brighten_fill_hsv(state.stroke, amount),
        stroke2=_brighten_fill_hsv(state.stroke2, amount),
        shadow=_brighten_fill_hsv(state.shadow, amount),
    )


def _solid_scanline_state(color: str) -> KaraokeColorState:
    fill = _solid_scanline_fill(color)
    return KaraokeColorState(text=fill, stroke=fill, stroke2=fill, shadow=fill)


def _state_clip(
    rect: QRectF, front: float, *, vertical: bool, rtl: bool, after: bool
) -> QRectF:
    """Clip one side of the moving front to its actual before/after colour."""

    after_is_far_side = rtl
    far_side = after == after_is_far_side
    extent = 1_000_000.0
    if vertical:
        return (
            QRectF(rect.left() - extent, front, rect.width() + extent * 2.0, extent)
            if far_side
            else QRectF(
                rect.left() - extent,
                -extent,
                rect.width() + extent * 2.0,
                front + extent,
            )
        )
    return (
        QRectF(front, rect.top() - extent, extent, rect.height() + extent * 2.0)
        if far_side
        else QRectF(
            -extent, rect.top() - extent, front + extent, rect.height() + extent * 2.0
        )
    )


def paint_scanline_strip(
    painter: QPainter,
    path: QPainterPath,
    rect: QRectF,
    *,
    front: float,
    params: ScanlineParams,
    style: Style,
    vertical: bool = False,
    rtl: bool = False,
    colors: KaraokeColors | None = None,
    opacity: float = 1.0,
) -> None:
    """Paint one glyph path's scan-line highlight band centred at ``front``.

    The glyph footprint inside the band is repainted through an inner alpha
    falloff. No pixels are expanded outside the glyph geometry, so counters
    and gaps between adjacent glyphs remain transparent.
    ``path`` must already be in device space when a utopia transform is active;
    pass the mapped ``front`` accordingly (see
    :func:`map_front_through_transform`).
    """

    stroke_width = style.stroke_width_px
    stroke2_width = main_stroke2_width(style)
    band = scanline_band_rect(rect, front, params, vertical=vertical)
    if not band_touches_rect(band, path.boundingRect(), 0.0):
        return
    if params.is_brighten and params.brightness <= 0.0:
        return
    opacity = max(0.0, min(opacity, 1.0))
    if opacity <= 0.0:
        return
    if params.is_brighten and colors is not None:
        states = (
            (False, _brighten_state_hsv(colors.before, params.brightness)),
            (True, _brighten_state_hsv(colors.after, params.brightness)),
        )
    else:
        solid = _solid_scanline_state(params.color)
        states = ((False, solid), (True, solid))
    for after, state in states:
        painter.save()
        try:
            painter.setOpacity(max(painter.opacity() * opacity, 0.0))
            painter.setClipRect(
                _state_clip(rect, front, vertical=vertical, rtl=rtl, after=after)
            )
            _paint_strip_body(
                painter,
                path,
                rect,
                band=band,
                state=state,
                params=params,
                style=style,
                stroke_width=stroke_width,
                stroke2_width=stroke2_width,
                vertical=vertical,
                front=front,
            )
        finally:
            painter.restore()


def _paint_strip_body(
    painter: QPainter,
    path: QPainterPath,
    rect: QRectF,
    *,
    band: QRectF,
    state: KaraokeColorState,
    params: ScanlineParams,
    style: Style,
    stroke_width: int,
    stroke2_width: int,
    vertical: bool,
    front: float,
) -> None:
    del band, style
    for slice_rect, alpha in scanline_feather_slices(
        rect, front, params, vertical=vertical
    ):
        painter.save()
        try:
            painter.setOpacity(painter.opacity() * alpha)
            painter.setClipRect(slice_rect)
            if stroke2_width > 0:
                paint_stroke_path(
                    painter,
                    path,
                    state.stroke2,
                    rect,
                    stroke2_pen_width(stroke_width, stroke2_width),
                )
            if stroke_width > 0:
                paint_stroke_path(
                    painter,
                    path,
                    state.stroke,
                    rect,
                    stroke_pen_width(stroke_width),
                )
            paint_fill_path(painter, path, state.text, rect)
        finally:
            painter.restore()


def map_front_through_transform(
    front: float,
    baseline_y: float,
    transform: QTransform | None,
) -> float:
    """Map a logical wipe-front x through a utopia character transform."""

    if transform is None or transform.isIdentity():
        return front
    return float(transform.map(QPointF(front, baseline_y)).x())


def main_scanline_front(
    segments: list,
    t_ms: int,
    rtl: bool,
) -> float | None:
    """Return the main-text wipe front, or ``None`` when no front is moving.

    ``None`` covers both "nothing sung yet" and "the whole line is complete":
    the highlight only exists while the front travels through the line.
    """

    band = fill_clip_band(segments, t_ms, rtl)
    if band is None:
        return None
    if all(segment_fill_ratio(segment, t_ms) >= 1.0 for segment in segments):
        return None
    return float(band[0] if rtl else band[1])


__all__ = [
    "ScanlineParams",
    "band_touches_rect",
    "main_scanline_front",
    "map_front_through_transform",
    "paint_scanline_strip",
    "scanline_feather_slices",
    "scanline_band_rect",
    "scanline_params_for_style",
]
