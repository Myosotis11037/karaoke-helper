"""Stable value signatures for caches backed by mutable project models."""

from __future__ import annotations

from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Hashable

from krok_helper.subtitle_render.domain.timing import GuideSymbol


_SIG_FIELD_NAMES_BY_TYPE: dict[type, tuple[str, ...]] = {}
_SIG_EXCLUDED_NAMES_BY_TYPE: dict[Hashable, tuple[str, ...]] = {}

_LYRIC_LAYOUT_EXCLUDED_STYLE_FIELDS = frozenset({
    "title_overlays",
    "hidden_builtin_layout_ids",
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
})
"""纯绘制字段：不参与歌词行的排版/分页/布局计划。

顶层清单还包含只喂标题层的字段。角色覆盖里的颜色需要递归剔除，否则
顶层颜色虽被忽略，嵌套的 ``SubtitleStyleScheme`` 仍会使整轨缓存失效。
剔除清单必须保守：字体、字号、描边宽和 ruby 尺寸等几何字段仍参与签名。
"""


def value_signature(value) -> Hashable:
    """Recursively describe the current value without using object identity."""

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


def lyric_layout_style_signature(style) -> Hashable:
    """Style signature restricted to fields that can affect lyric layout.

    与 :func:`value_signature` 的区别：剔除 ``_LYRIC_LAYOUT_EXCLUDED_STYLE_FIELDS``
    列出的纯标题字段。用于歌词行布局 / 显示行解析 / 页偏移 / 布局计划等
    缓存的 key——标题属性编辑不再整份作废这些缓存。
    局部复用永远以本签名为准（签名不匹配即回退重建），调用方传入的
    「只改了标题/颜色」只是性能提示，不是正确性依据。
    """
    return _lyric_layout_value_signature(style, root=True)


def _lyric_layout_value_signature(value, *, root: bool = False) -> Hashable:
    """Recursively sign layout inputs while omitting nested paint-only fields."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, GuideSymbol):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_lyric_layout_value_signature(item) for item in value)
    if isinstance(value, dict):
        return tuple(
            (key, _lyric_layout_value_signature(item))
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if is_dataclass(value) and not isinstance(value, type):
        value_type = type(value)
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
            _lyric_layout_value_signature(getattr(value, name)) for name in names
        )
    return repr(value)


__all__ = [
    "lyric_layout_style_signature",
    "value_signature",
]
