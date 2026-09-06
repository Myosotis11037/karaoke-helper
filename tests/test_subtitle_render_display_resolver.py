from __future__ import annotations

from dataclasses import replace

from krok_helper.subtitle_render.engine.layout.display.resolver import (
    DisplayResolutionCache,
    DisplayResolutionPorts,
    StyleDisplayResolutionPorts,
    clear_display_line_resolution_cache,
    resolve_display_lines,
    resolve_display_lines_for_style,
    resolve_display_timing,
)
from krok_helper.subtitle_render.engine.timing.timeline import DisplayLine
from krok_helper.subtitle_render.domain.models import (
    Style,
    TimingChar,
    TimingLine,
)
from krok_helper.subtitle_render.domain.timing import TimingTrack


def test_display_resolution_cache_returns_copies_and_evicts_lru_entry() -> None:
    cache = DisplayResolutionCache(max_items=2)
    first_owner = object()
    second_owner = object()
    third_owner = object()
    cache.put("first", first_owner, ["one"])
    cache.put("second", second_owner, ["two"])

    first = cache.get("first")
    assert first == ["one"]
    first.append("changed")
    assert cache.get("first") == ["one"]

    cache.put("third", third_owner, ["three"])
    assert cache.get("second") is None
    assert cache.get("first") == ["one"]
    assert cache.get("third") == ["three"]

    cache.clear()
    assert cache.get("first") is None


def test_style_display_resolver_owns_canvas_normalization_and_cache() -> None:
    clear_display_line_resolution_cache()
    track = TimingTrack()
    style = Style(
        layout_reference_height=720,
        allow_inter_page_line_overlap=True,
    )
    events: list[object] = []

    def build(width, height, base_kwargs):
        events.append((width, height, dict(base_kwargs)))
        return DisplayResolutionPorts(
            compute=lambda **_kwargs: ["ideal"],
            resolve_timing=lambda items, _enforce, fill_section_time=None: list(
                items
            ),
            collision_pairs=lambda _items: (),
            secondary_collision_pairs=lambda _items: (),
            fill_section_time=lambda items: items,
            apply_animation_guard=lambda items, _enforce: items,
        )

    ports = StyleDisplayResolutionPorts(build=build)
    first = resolve_display_lines_for_style(
        track,
        style,
        {"sync_entry": True, "auto_fill_section_time": True},
        ports,
    )
    again = resolve_display_lines_for_style(
        track,
        style,
        {"sync_entry": True, "auto_fill_section_time": True},
        ports,
    )

    assert first == ["ideal"]
    assert again == ["ideal"]
    assert events == [
        (
            1280,
            720,
            {
                "sync_entry": False,
                "sync_ending": False,
                "auto_fill_section_time": False,
            },
        )
    ]
    clear_display_line_resolution_cache()


def test_display_resolver_skips_collision_discovery_when_overlap_is_allowed() -> None:
    events: list[object] = []

    def compute(**kwargs):
        events.append(("compute", kwargs))
        return ["ideal"]

    ports = DisplayResolutionPorts(
        compute=compute,
        resolve_timing=lambda items, enforce, fill_section_time=None: (
            events.append(("timing", tuple(items), enforce)) or ["resolved"]
        ),
        collision_pairs=lambda _items: (_ for _ in ()).throw(
            AssertionError("collision discovery must be skipped")
        ),
        secondary_collision_pairs=lambda _items: (_ for _ in ()).throw(
            AssertionError("secondary discovery must be skipped")
        ),
        fill_section_time=lambda items: items,
        apply_animation_guard=lambda items, enforce: (
            events.append(("guard", tuple(items), enforce)) or list(items)
        ),
    )

    resolved = resolve_display_lines(
        avoid_collisions=False,
        ports=ports,
    )

    assert resolved == ["resolved"]
    assert events == [
        (
            "compute",
            {
                "adjust_same_position": False,
                "dynamic_single_page_reflow": True,
                "independent_line_entry": True,
            },
        ),
        ("timing", ("ideal",), False),
        ("guard", ("resolved",), False),
    ]


def test_display_resolver_preserves_collision_pass_order() -> None:
    events: list[object] = []
    computed = iter((["ideal"], ["forced"], ["squeezed"], ["secondary"]))
    discovered = iter((((0, 1),), ((1, 2),)))

    def compute(**kwargs):
        events.append(("compute", kwargs))
        return list(next(computed))

    def resolve_timing(items, enforce, fill_section_time=None):
        events.append(("timing", tuple(items), enforce))
        return list(items)

    ports = DisplayResolutionPorts(
        compute=compute,
        resolve_timing=resolve_timing,
        collision_pairs=lambda items: (
            events.append(("pairs", tuple(items))) or next(discovered)
        ),
        secondary_collision_pairs=lambda items: (
            events.append(("secondary_pairs", tuple(items))) or ((2, 3),)
        ),
        fill_section_time=lambda items: items,
        apply_animation_guard=lambda items, _enforce: items,
    )

    resolved = resolve_display_lines(
        avoid_collisions=True,
        ports=ports,
    )

    assert resolved == ["secondary"]
    compute_events = [event for event in events if event[0] == "compute"]
    assert compute_events[1][1]["force_bottom_pairs"] == ((0, 1),)
    assert compute_events[2][1]["squeeze_pairs"] == ((1, 2),)
    assert compute_events[3][1]["squeeze_pairs"] == ((1, 2), (2, 3))
    assert [event for event in events if event[0] == "timing"] == [
        ("timing", ("ideal",), True),
        ("timing", ("forced",), True),
        ("timing", ("squeezed",), True),
        ("timing", ("secondary",), True),
    ]


def test_display_resolver_runs_standing_guard_after_the_last_timing_pass() -> None:
    """与填充无关的收尾守卫：最后一轮 timing 之后无条件再兜一次底。"""

    events: list[object] = []
    computed = iter((["ideal"],))

    ports = DisplayResolutionPorts(
        compute=lambda **_kwargs: next(computed),
        resolve_timing=lambda items, enforce, fill_section_time=None: (
            events.append(("timing", tuple(items))) or ["timed"]
        ),
        collision_pairs=lambda _items: (),
        secondary_collision_pairs=lambda _items: (),
        fill_section_time=lambda items: items,
        apply_animation_guard=lambda items, enforce: (
            events.append(("guard", tuple(items), enforce)) or ["guarded"]
        ),
    )

    resolved = resolve_display_lines(
        avoid_collisions=True,
        ports=ports,
    )

    assert resolved == ["guarded"]
    assert events == [("timing", ("ideal",)), ("guard", ("timed",), True)]


def test_resolve_display_timing_fills_expected_times_before_guard() -> None:
    """填充属于预期时间阶段：同步之后、守卫之前，守卫看到的就是填充后的窗口。"""

    lines = [
        TimingLine(chars=[TimingChar("あ", 1_000)], end_ms=2_000),
        TimingLine(chars=[TimingChar("い", 1_500)], end_ms=2_500),
    ]
    display_lines = [
        DisplayLine(lines[0], 0, 0, 3_000, 0, 0, 1),
        DisplayLine(lines[1], 1, 0, 3_500, 0, 0, 1),
    ]
    events: list[object] = []

    class _Ports:
        @staticmethod
        def entry_animation_ms(_line):
            return 0

        @staticmethod
        def exit_animation_ms(_line):
            return 0

        @staticmethod
        def measure(items, _time_window):
            events.append(
                ("measure", tuple(int(item.display_end_ms) for item in items))
            )
            return []

        @staticmethod
        def retime(_measured, _items, _indices, _time_window):
            return None

    def fill(items):
        events.append(("fill",))
        return [
            replace(item, display_end_ms=item.display_end_ms + 1_000)
            for item in items
        ]

    resolved = resolve_display_timing(
        Style(),
        display_lines,
        _Ports(),
        enforce_inter_page_gap=True,
        fill_section_time=fill,
    )

    # 守卫的测量发生在填充之后，看到的是填充延长后的窗口；同步退场
    # （默认开）先把两行结尾对齐到页内最晚，再由填充整体 +1000。
    assert events == [
        ("measure", (3_000, 3_500)),
        ("fill",),
        ("measure", (4_500, 4_500)),
    ]
    assert [item.display_end_ms for item in resolved] == [4_500, 4_500]
