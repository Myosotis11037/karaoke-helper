"""Stable value signatures for caches backed by mutable project models."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Hashable

from krok_helper.subtitle_render.domain.timing import GuideSymbol
from krok_helper.subtitle_render.engine.render_progress import yield_to_gui


_SIG_FIELD_NAMES_BY_TYPE: dict[type, tuple[str, ...]] = {}
_SIG_EXCLUDED_NAMES_BY_TYPE: dict[Hashable, tuple[str, ...]] = {}

_LYRIC_LAYOUT_TITLE_ONLY_EXCLUDED_STYLE_FIELDS = frozenset({
    "title_overlays",
    "hidden_builtin_layout_ids",
})
_LYRIC_LAYOUT_EXCLUDED_STYLE_FIELDS = _LYRIC_LAYOUT_TITLE_ONLY_EXCLUDED_STYLE_FIELDS | frozenset({
    "base_color",
    "fill_color",
    "fill_gradient_enabled",
    "fill_gradient_start_color",
    "fill_gradient_end_color",
    "fill_gradient_angle_deg",
    "stroke_color",
    "shadow_color",
    "karaoke_colors",
    "ruby_color",
    "ruby_colors_follow_main",
    "ruby_karaoke_colors",
    "lit_fill_color",
    "lit1_fill_color",
    "lit2_fill_color",
    "lit3_fill_color",
    "lit_stroke_color",
    "volume_fill_color",
    "volume_stroke_color",
    "volume_overlay_fill_color",
    "volume_overlay_stroke_color",
})
_LYRIC_LAYOUT_EXCLUDED_SCHEME_FIELDS = frozenset({
    "base_color",
    "fill_color",
    "fill_gradient_enabled",
    "fill_gradient_start_color",
    "fill_gradient_end_color",
    "fill_gradient_angle_deg",
    "stroke_color",
    "shadow_color",
    "ruby_color",
    "karaoke_colors",
    "ruby_colors_follow_main",
    "ruby_karaoke_colors",
    # 装饰（阴影/发光）与注音渐变共享是纯绘制字段：跨页避让的碰撞包络
    # 按纯主字形 path 测量（Ruby、描边、阴影、发光全部刻意排除），
    # 装饰参数不影响排版。
    "decoration_kind",
    "shadow_offset_x",
    "shadow_offset_y",
    "glow_radius_px",
    "glow_before_radius_px",
    "glow_after_radius_px",
    "glow_concentration_level",
    "ruby_decoration_kind",
    "ruby_shadow_offset_x",
    "ruby_shadow_offset_y",
    "ruby_glow_radius_px",
    "ruby_glow_before_radius_px",
    "ruby_glow_after_radius_px",
    "ruby_glow_concentration_level",
    "ruby_horizontal_gradient_with_main",
})
"""纯绘制字段：不参与歌词行的排版/分页/布局计划。

顶层清单还包含只喂标题层的字段。角色覆盖里的颜色需要递归剔除，否则
顶层颜色虽被忽略，嵌套的 ``SubtitleStyleScheme`` 仍会使整轨缓存失效。
剔除清单必须保守：字体、字号、描边宽和 ruby 尺寸等几何字段仍参与签名。
"""


def value_signature(value) -> Hashable:
    """Recursively describe the current value without using object identity."""

    # 整轨签名是热路径（布局计划缓存每次查找都要重算），长曲目的递归
    # 遍历不释放 GIL 会饿到 GUI 线程；节流后的让出近乎零开销。
    yield_to_gui()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # GuideSymbol is the only frozen model carrying a potentially very large
    # immutable tuple (the complete SVG outline). Reusing the value as the key
    # avoids recursively copying every path command on every cache lookup.
    if isinstance(value, GuideSymbol):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(value_signature(item) for item in value)
    if isinstance(value, dict):
        return tuple(
            (key, value_signature(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        names = _SIG_FIELD_NAMES_BY_TYPE.get(value_type)
        if names is None:
            names = tuple(field.name for field in dataclass_fields(value))
            _SIG_FIELD_NAMES_BY_TYPE[value_type] = names
        return (value_type.__name__,) + tuple(
            value_signature(getattr(value, name)) for name in names
        )
    return repr(value)


def lyric_layout_style_signature(style, *, include_paint_fields: bool = False) -> Hashable:
    """Style signature restricted to fields that can affect lyric layout.

    与 :func:`value_signature` 的区别：剔除 ``_LYRIC_LAYOUT_EXCLUDED_STYLE_FIELDS``
    列出的纯标题字段。用于歌词行布局 / 显示行解析 / 页偏移 / 布局计划等
    缓存的 key——标题属性编辑不再整份作废这些缓存。
    局部复用永远以本签名为准（签名不匹配即回退重建），调用方传入的
    「只改了标题/颜色」只是性能提示，不是正确性依据。

    ``include_paint_fields=True`` 时保留颜色/填充类字段（仅剔除标题字段）：
    供**缓存值携带样式引用**的消费者使用（如 CPU 行布局缓存的
    ``_LineLayout.glyphs`` 各自带解析后的有效样式）——它们的 key 必须能
    区分任何改变绘制结果的输入，否则换色命中旧布局会用旧样式绘制。
    布局计划 / 显示行解析 / 页偏移缓存的值是纯几何与时间结构，用默认
    的颜色剔除版即可在颜色编辑时安全复用。
    """
    return _lyric_layout_value_signature(
        style, root=True, include_paint_fields=include_paint_fields
    )


def _lyric_layout_value_signature(
    value, *, root: bool = False, include_paint_fields: bool = False
) -> Hashable:
    """Recursively sign layout inputs while omitting nested paint-only fields."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, GuideSymbol):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _lyric_layout_value_signature(
                item, include_paint_fields=include_paint_fields
            )
            for item in value
        )
    if isinstance(value, dict):
        return tuple(
            (
                key,
                _lyric_layout_value_signature(
                    item, include_paint_fields=include_paint_fields
                ),
            )
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
        if include_paint_fields:
            excluded = (
                _LYRIC_LAYOUT_TITLE_ONLY_EXCLUDED_STYLE_FIELDS if root else frozenset()
            )
        else:
            excluded = (
                _LYRIC_LAYOUT_EXCLUDED_STYLE_FIELDS
                if root
                else _LYRIC_LAYOUT_EXCLUDED_SCHEME_FIELDS
                if value_type.__name__ == "SubtitleStyleScheme"
                else frozenset()
            )
        cache_key = (value_type, excluded)
        names = _SIG_EXCLUDED_NAMES_BY_TYPE.get(cache_key)
        if names is None:
            names = tuple(
                field.name
                for field in dataclass_fields(value)
                if field.name not in excluded
            )
            _SIG_EXCLUDED_NAMES_BY_TYPE[cache_key] = names
        return (value_type.__name__,) + tuple(
            _lyric_layout_value_signature(
                getattr(value, name), include_paint_fields=include_paint_fields
            )
            for name in names
        )
    return repr(value)


__all__ = [
    "lyric_layout_style_signature",
    "value_signature",
]
