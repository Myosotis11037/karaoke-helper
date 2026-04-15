from __future__ import annotations

import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from krok_helper.audio_alignment import (
    ENCODE_MODE_HARDWARE,
    ENCODE_MODE_SOFTWARE,
    WaveformData,
    default_aligned_audio_path,
    default_aligned_video_path,
    export_aligned_audio,
    export_aligned_video,
    extract_waveform,
    format_offset,
)
from krok_helper.config import (
    APP_TITLE,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
)
from krok_helper.errors import ProcessingError
from krok_helper.pipeline import (
    DEFAULT_OFF_NAME_TEMPLATE,
    DEFAULT_ON_NAME_TEMPLATE,
    OUTPUT_NAME_MODE_FIXED,
    OUTPUT_NAME_MODE_TEMPLATE,
    OUTPUT_NAME_MODE_VIDEO_NAME,
    resolve_output_dir,
    run_pipeline,
    validate_output_name_template,
)
from krok_helper.settings import AppSettings, load_app_settings, save_app_settings
from krok_helper.windows import WindowsFileDropHandler


VIDEO_FILETYPES = [("视频文件", "*.mkv *.mp4 *.mov *.avi"), ("所有文件", "*.*")]
AUDIO_FILETYPES = [
    ("音频文件", "*.flac *.wav *.m4a *.aac *.ape *.alac *.mkv"),
    ("所有文件", "*.*"),
]
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".avi"}
AUDIO_EXTENSIONS = {".flac", ".wav", ".m4a", ".aac", ".ape", ".alac", ".mkv"}
FFMPEG_DIR_PLACEHOLDER = "未设置，将优先使用系统 PATH 中的 ffmpeg"
ALIGN_TARGET_VIDEO = "video"
ALIGN_TARGET_AUDIO = "audio"
OUTPUT_NAME_MODE_LABELS = {
    OUTPUT_NAME_MODE_FIXED: "默认命名: on_vocal.mkv / off_vocal.mkv",
    OUTPUT_NAME_MODE_TEMPLATE: "自定义模板: 使用你自己的命名范式",
}


class DropZone:
    def __init__(
        self,
        parent,
        *,
        title: str,
        hint: str,
        extensions: set[str],
        on_click,
    ) -> None:
        self.extensions = {ext.lower() for ext in extensions}
        self.on_click = on_click
        self.path: Path | None = None

        self.path_var = tk.StringVar(value="未选择文件")
        self.frame = tk.Frame(
            parent,
            bg="#f6f8fb",
            bd=1,
            relief="solid",
            highlightthickness=2,
            highlightbackground="#d5dce6",
            highlightcolor="#2f6fed",
            cursor="hand2",
            padx=16,
            pady=14,
        )

        self.title_label = tk.Label(
            self.frame,
            text=title,
            bg="#f6f8fb",
            fg="#1f2937",
            font=("Microsoft YaHei UI", 12, "bold"),
            anchor="w",
        )
        self.title_label.pack(fill="x")

        self.hint_label = tk.Label(
            self.frame,
            text=hint,
            bg="#f6f8fb",
            fg="#5b6677",
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
            wraplength=1,
        )
        self.hint_label.pack(fill="x", pady=(8, 12))

        self.path_label = tk.Label(
            self.frame,
            textvariable=self.path_var,
            bg="#f6f8fb",
            fg="#111827",
            font=("Consolas", 10),
            justify="left",
            anchor="nw",
            wraplength=1,
        )
        self.path_label.pack(fill="both", expand=True)

        self.action_label = tk.Label(
            self.frame,
            text="点击选择文件，或直接拖进这个区域",
            bg="#f6f8fb",
            fg="#2f6fed",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
            justify="left",
            wraplength=1,
        )
        self.action_label.pack(fill="x", pady=(12, 0))

        self._bind_clicks(self.frame)
        self.frame.bind("<Configure>", self._handle_resize, add="+")
        self._set_visual_state(selected=False)

    def _handle_resize(self, event) -> None:
        wraplength = max(event.width - 36, 120)
        self.hint_label.configure(wraplength=wraplength)
        self.path_label.configure(wraplength=wraplength)
        self.action_label.configure(wraplength=wraplength)

    def _bind_clicks(self, widget) -> None:
        widget.bind("<Button-1>", lambda _event: self.on_click(), add="+")
        widget.bind("<Enter>", lambda _event: self._set_hover(True), add="+")
        widget.bind("<Leave>", lambda _event: self._set_hover(False), add="+")
        for child in widget.winfo_children():
            self._bind_clicks(child)

    def _set_hover(self, hovered: bool) -> None:
        if self.path is not None:
            return
        if hovered:
            self.frame.configure(bg="#eef4ff", highlightbackground="#8aa8f8")
            for child in self.frame.winfo_children():
                child.configure(bg="#eef4ff")
        else:
            self._set_visual_state(selected=False)

    def _set_visual_state(self, *, selected: bool) -> None:
        bg = "#ecfdf3" if selected else "#f6f8fb"
        border = "#3aa76d" if selected else "#d5dce6"
        accent = "#177245" if selected else "#2f6fed"
        self.frame.configure(bg=bg, highlightbackground=border)
        for child in self.frame.winfo_children():
            child.configure(bg=bg)
        self.action_label.configure(fg=accent)

    def accepts(self, path: Path) -> bool:
        return path.suffix.lower() in self.extensions and path.is_file()

    def set_path(self, path: Path) -> None:
        self.path = path
        self.path_var.set(str(path))
        self._set_visual_state(selected=True)

    def contains_widget(self, widget) -> bool:
        current = widget
        while current is not None:
            if current == self.frame:
                return True
            current = getattr(current, "master", None)
        return False


class WaveformViewer:
    def __init__(
        self,
        parent,
        *,
        mode_var: tk.StringVar,
        target_var: tk.StringVar,
        on_offset_changed,
    ) -> None:
        self.mode_var = mode_var
        self.target_var = target_var
        self.on_offset_changed = on_offset_changed
        self.video_waveform: WaveformData | None = None
        self.audio_waveform: WaveformData | None = None
        self.offset_seconds = 0.0
        self.view_start_seconds = 0.0
        self.pixels_per_second = 120.0
        self.label_gutter_width = 190
        self._drag_start_x = 0
        self._drag_start_offset = 0.0
        self._drag_start_view = 0.0
        self._drag_active = False

        self.canvas = tk.Canvas(
            parent,
            bg="#ffffff",
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#d5dce6",
            cursor="crosshair",
        )
        self.canvas.bind("<Configure>", lambda _event: self.draw(), add="+")
        self.canvas.bind("<MouseWheel>", self._handle_mousewheel, add="+")
        self.canvas.bind("<Button-4>", self._handle_mousewheel, add="+")
        self.canvas.bind("<Button-5>", self._handle_mousewheel, add="+")
        self.canvas.bind("<ButtonPress-1>", self._handle_drag_start, add="+")
        self.canvas.bind("<B1-Motion>", self._handle_drag, add="+")

    def set_waveforms(
        self,
        *,
        video_waveform: WaveformData | None = None,
        audio_waveform: WaveformData | None = None,
    ) -> None:
        if video_waveform is not None:
            self.video_waveform = video_waveform
        if audio_waveform is not None:
            self.audio_waveform = audio_waveform
        self.view_start_seconds = 0.0
        self.offset_seconds = 0.0
        self.on_offset_changed(self.offset_seconds)
        self.draw()

    def clear(self) -> None:
        self.video_waveform = None
        self.audio_waveform = None
        self.view_start_seconds = 0.0
        self.offset_seconds = 0.0
        self.on_offset_changed(self.offset_seconds)
        self.draw()

    def set_offset(self, seconds: float) -> None:
        self.offset_seconds = seconds
        self.on_offset_changed(self.offset_seconds)
        self.draw()

    def nudge_offset(self, delta_seconds: float) -> None:
        self.set_offset(self.offset_seconds + delta_seconds)

    def set_zoom(self, pixels_per_second: float) -> None:
        plot_left, plot_width = self._plot_bounds()
        self._zoom_to(pixels_per_second, plot_left + plot_width / 2)

    def reset_view(self) -> None:
        self.view_start_seconds = 0.0
        self.draw()

    def _handle_drag_start(self, event) -> None:
        plot_left, _plot_width = self._plot_bounds()
        self._drag_active = event.x >= plot_left
        if not self._drag_active:
            return

        self._drag_start_x = event.x
        self._drag_start_offset = self.offset_seconds
        self._drag_start_view = self.view_start_seconds

    def _handle_drag(self, event) -> None:
        if not self._drag_active:
            return

        delta_seconds = (event.x - self._drag_start_x) / self.pixels_per_second
        if self.mode_var.get() == "pan":
            self.view_start_seconds = max(0.0, self._drag_start_view - delta_seconds)
            self.draw()
            return

        self.offset_seconds = self._drag_start_offset + delta_seconds
        self.on_offset_changed(self.offset_seconds)
        self.draw()

    def _handle_mousewheel(self, event) -> None:
        direction = 0
        if getattr(event, "delta", 0):
            direction = 1 if event.delta > 0 else -1
        elif getattr(event, "num", None) == 4:
            direction = 1
        elif getattr(event, "num", None) == 5:
            direction = -1
        if not direction:
            return

        factor = 1.18 if direction > 0 else 1 / 1.18
        self._zoom_to(self.pixels_per_second * factor, event.x)

    def _zoom_to(self, pixels_per_second: float, anchor_x: float) -> None:
        plot_left, plot_width = self._plot_bounds()
        anchor_x = min(plot_left + plot_width, max(plot_left, anchor_x))
        old_pixels_per_second = self.pixels_per_second
        self.pixels_per_second = min(800.0, max(8.0, pixels_per_second))
        anchor_time = self.view_start_seconds + (anchor_x - plot_left) / old_pixels_per_second
        self.view_start_seconds = max(0.0, anchor_time - (anchor_x - plot_left) / self.pixels_per_second)
        self.draw()

    def _plot_bounds(self) -> tuple[int, int]:
        width = max(1, self.canvas.winfo_width())
        plot_left = min(self.label_gutter_width, max(0, width - 120))
        return plot_left, max(1, width - plot_left)

    def _visible_duration(self) -> float:
        _plot_left, plot_width = self._plot_bounds()
        return plot_width / self.pixels_per_second

    def _is_video_target(self) -> bool:
        return self.target_var.get() == ALIGN_TARGET_VIDEO

    def _max_duration(self) -> float:
        durations = []
        if self.video_waveform is not None:
            extra = max(0.0, self.offset_seconds) if self._is_video_target() else 0.0
            durations.append(self.video_waveform.duration + extra)
        if self.audio_waveform is not None:
            extra = max(0.0, self.offset_seconds) if not self._is_video_target() else 0.0
            durations.append(self.audio_waveform.duration + extra)
        return max(durations, default=0.0)

    def _nice_grid_interval(self) -> float:
        target = self._visible_duration() / 8
        for candidate in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if candidate >= target:
                return candidate
        return 600

    def _format_time(self, seconds: float) -> str:
        seconds = max(0.0, seconds)
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        if minutes:
            return f"{minutes}:{remainder:04.1f}"
        return f"{remainder:.1f}s"

    def _draw_grid(self, width: int, height: int, plot_left: int) -> None:
        interval = self._nice_grid_interval()
        first_tick = int(self.view_start_seconds / interval) * interval
        tick = first_tick
        while tick <= self.view_start_seconds + self._visible_duration() + interval:
            x = plot_left + int((tick - self.view_start_seconds) * self.pixels_per_second)
            if 0 <= x <= width:
                self.canvas.create_line(x, 0, x, height, fill="#eef2f7")
                self.canvas.create_text(
                    x + 4,
                    14,
                    text=self._format_time(tick),
                    anchor="w",
                    fill="#6b7280",
                    font=("Segoe UI", 9),
                )
            tick += interval

    def _draw_waveform(
        self,
        waveform: WaveformData,
        *,
        top: int,
        bottom: int,
        color: str,
        label: str,
        timeline_offset: float,
    ) -> None:
        width = max(1, self.canvas.winfo_width())
        plot_left, plot_width = self._plot_bounds()
        center_y = (top + bottom) // 2
        amplitude = max(12, (bottom - top) // 2 - 22)
        self.canvas.create_rectangle(0, top, plot_left, bottom, fill="#ffffff", outline="")
        self.canvas.create_text(
            14,
            center_y - 11,
            text=label,
            anchor="w",
            fill="#1f2937",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.canvas.create_line(plot_left, center_y, width, center_y, fill="#d5dce6")
        self.canvas.create_line(plot_left, top + 6, plot_left, bottom - 6, fill="#e5e7eb")

        if not waveform.peaks:
            return

        step = 2
        peaks = waveform.peaks
        peaks_per_second = waveform.peaks_per_second
        for x in range(plot_left, width + step, step):
            plot_x = x - plot_left
            timeline_start = self.view_start_seconds + plot_x / self.pixels_per_second
            timeline_end = self.view_start_seconds + (plot_x + step) / self.pixels_per_second
            local_start = timeline_start - timeline_offset
            local_end = timeline_end - timeline_offset
            if local_end < 0 or local_start > waveform.duration:
                continue

            start_index = max(0, int(local_start * peaks_per_second))
            end_index = min(len(peaks), max(start_index + 1, int(local_end * peaks_per_second) + 1))
            peak = max(peaks[start_index:end_index], default=0.0)
            y1 = center_y - int(peak * amplitude)
            y2 = center_y + int(peak * amplitude)
            self.canvas.create_line(x, y1, x, y2, fill=color)

        start_x = plot_left + int((timeline_offset - self.view_start_seconds) * self.pixels_per_second)
        if plot_left <= start_x <= width:
            self.canvas.create_line(start_x, top + 6, start_x, bottom - 6, fill=color, dash=(4, 3))

    def draw(self) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, width, height, fill="#ffffff", outline="")

        if self.video_waveform is None and self.audio_waveform is None:
            self.canvas.create_text(
                width // 2,
                height // 2,
                text="选择文件后生成波形",
                fill="#6b7280",
                font=("Microsoft YaHei UI", 12, "bold"),
            )
            return

        plot_left, _plot_width = self._plot_bounds()
        self.canvas.create_rectangle(0, 0, plot_left, height, fill="#ffffff", outline="")
        split = height // 2
        self.canvas.create_rectangle(plot_left, 0, width, split, fill="#f8fafc", outline="")
        self.canvas.create_rectangle(plot_left, split, width, height, fill="#fffaf1", outline="")
        self._draw_grid(width, height, plot_left)
        self.canvas.create_line(0, split, width, split, fill="#d5dce6")

        if self.video_waveform is not None:
            video_target = self._is_video_target()
            self._draw_waveform(
                self.video_waveform,
                top=0,
                bottom=split,
                color="#2563eb",
                label=(
                    f"字幕视频音轨 {format_offset(self.offset_seconds)}"
                    if video_target
                    else "字幕视频音轨"
                ),
                timeline_offset=self.offset_seconds if video_target else 0.0,
            )
        if self.audio_waveform is not None:
            audio_target = not self._is_video_target()
            self._draw_waveform(
                self.audio_waveform,
                top=split,
                bottom=height,
                color="#dc6b21",
                label=(
                    f"原唱音源 {format_offset(self.offset_seconds)}"
                    if audio_target
                    else "原唱音源"
                ),
                timeline_offset=self.offset_seconds if audio_target else 0.0,
            )

        zero_x = plot_left + int((0 - self.view_start_seconds) * self.pixels_per_second)
        if plot_left <= zero_x <= width:
            self.canvas.create_line(zero_x, 0, zero_x, height, fill="#111827", dash=(2, 3))

        max_duration = self._max_duration()
        if max_duration:
            right_edge = max_duration - self._visible_duration()
            if self.view_start_seconds > right_edge > 0:
                self.view_start_seconds = right_edge


class KaraokeHiresApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(self._build_centered_geometry(WINDOW_WIDTH, WINDOW_HEIGHT))
        self.root.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.root.configure(bg="#eef2f7")

        self.video_var = tk.StringVar()
        self.on_vocal_var = tk.StringVar()
        self.off_vocal_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value="跟随字幕视频所在目录")
        self.ffmpeg_dir_var = tk.StringVar(value=FFMPEG_DIR_PLACEHOLDER)
        self.output_name_mode_var = tk.StringVar(value=OUTPUT_NAME_MODE_FIXED)
        self.on_name_template_var = tk.StringVar(value=DEFAULT_ON_NAME_TEMPLATE)
        self.off_name_template_var = tk.StringVar(value=DEFAULT_OFF_NAME_TEMPLATE)
        self.status_var = tk.StringVar(value="准备就绪")
        self.align_video_var = tk.StringVar()
        self.align_audio_var = tk.StringVar()
        self.align_status_var = tk.StringVar(value="准备生成波形")
        self.align_offset_var = tk.StringVar(value="字幕视频偏移 +0.000s")
        self.align_target_var = tk.StringVar(value=ALIGN_TARGET_VIDEO)
        self.align_drag_mode_var = tk.StringVar(value="offset")
        self.align_encode_mode_var = tk.StringVar(value=ENCODE_MODE_SOFTWARE)
        self.align_zoom_var = tk.DoubleVar(value=120.0)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.align_log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.align_worker: threading.Thread | None = None
        self.align_export_worker: threading.Thread | None = None
        self.drop_handler: WindowsFileDropHandler | None = None
        self.settings_window: tk.Toplevel | None = None
        self.settings_canvas: tk.Canvas | None = None
        self.settings_scrollbar: ttk.Scrollbar | None = None
        self.settings_content_frame: ttk.Frame | None = None
        self.settings_canvas_window_id: int | None = None
        self.settings_status_var = tk.StringVar(value="")
        self.ffmpeg_display_label: tk.Label | None = None
        self.on_template_entry: ttk.Entry | None = None
        self.off_template_entry: ttk.Entry | None = None
        self.module_frames: dict[str, ttk.Frame] = {}
        self.module_buttons: dict[str, tk.Button] = {}
        self.active_module = ""
        self.align_viewer: WaveformViewer | None = None
        self.align_log_text: tk.Text | None = None
        self.align_move_radio: ttk.Radiobutton | None = None
        self.align_encode_row: ttk.Frame | None = None

        self._load_saved_settings()
        self._configure_styles()
        self._build_ui()
        self._update_output_template_state()
        self.root.after(100, self._drain_log_queue)
        self._install_file_drop()

    def _build_centered_geometry(self, width: int, height: int) -> str:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        return f"{width}x{height}+{x}+{y}"

    def _load_saved_settings(self) -> None:
        settings = load_app_settings()
        output_name_mode = settings.output_name_mode
        if output_name_mode == OUTPUT_NAME_MODE_VIDEO_NAME:
            output_name_mode = OUTPUT_NAME_MODE_TEMPLATE

        if output_name_mode not in OUTPUT_NAME_MODE_LABELS:
            output_name_mode = OUTPUT_NAME_MODE_FIXED

        self.output_name_mode_var.set(output_name_mode)
        self.on_name_template_var.set(settings.on_name_template or DEFAULT_ON_NAME_TEMPLATE)
        self.off_name_template_var.set(settings.off_name_template or DEFAULT_OFF_NAME_TEMPLATE)
        if settings.ffmpeg_dir.strip():
            self.ffmpeg_dir_var.set(settings.ffmpeg_dir.strip())

    def _configure_styles(self) -> None:
        default_font = ("Microsoft YaHei UI", 11)
        self.root.option_add("*Font", default_font)
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabel", background="#eef2f7", foreground="#1f2937", font=default_font)
        style.configure("TButton", padding=(14, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TRadiobutton", background="#eef2f7", foreground="#1f2937", font=default_font)
        style.configure("TProgressbar", thickness=10)

    def _build_module_button(self, parent, module_id: str, label: str) -> None:
        button = tk.Button(
            parent,
            text=label,
            command=lambda: self._show_module(module_id),
            anchor="w",
            bg="#111827",
            fg="#d1d5db",
            activebackground="#1f2937",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=18,
            pady=14,
            font=("Microsoft YaHei UI", 11, "bold"),
            cursor="hand2",
        )
        button.pack(fill="x", padx=10, pady=(0, 6))
        self.module_buttons[module_id] = button

    def _show_module(self, module_id: str) -> None:
        frame = self.module_frames.get(module_id)
        if frame is None:
            return

        frame.tkraise()
        self.active_module = module_id
        for current_id, button in self.module_buttons.items():
            is_active = current_id == module_id
            button.configure(
                bg="#2563eb" if is_active else "#111827",
                fg="#ffffff" if is_active else "#d1d5db",
                activebackground="#1d4ed8" if is_active else "#1f2937",
            )

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=0)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)

        sidebar = tk.Frame(shell, bg="#111827", width=180)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        tk.Label(
            sidebar,
            text="Krok Helper",
            bg="#111827",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="w",
            padx=16,
            pady=18,
        ).pack(fill="x")

        self._build_module_button(sidebar, "align", "波形对齐")
        self._build_module_button(sidebar, "hires", "Hi-Res 生成")

        content = ttk.Frame(shell)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        align_frame = ttk.Frame(content)
        hires_frame = ttk.Frame(content)
        for frame in (align_frame, hires_frame):
            frame.grid(row=0, column=0, sticky="nsew")
        self.module_frames = {"align": align_frame, "hires": hires_frame}

        self._build_alignment_ui(align_frame)
        self._build_generate_ui(hires_frame)
        self._show_module("align")

    def _build_generate_ui(self, parent) -> None:
        shell = ttk.Frame(parent, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1, minsize=280)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="卡拉 OK 字幕视频一键 Hi-Res 生成",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            header,
            text="把三个文件拖进下方卡片，或点击卡片选择文件。输出目录会自动使用字幕视频所在目录。",
            font=("Microsoft YaHei UI", 11),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        output_row = ttk.Frame(shell)
        output_row.grid(row=1, column=0, sticky="ew", pady=(18, 10))
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="输出目录", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(output_row, textvariable=self.output_dir_var, font=("Yu Gothic UI", 11)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )

        ffmpeg_row = ttk.Frame(shell)
        ffmpeg_row.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        ffmpeg_row.columnconfigure(1, weight=1)
        ttk.Label(ffmpeg_row, text="FFmpeg 目录", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(ffmpeg_row, textvariable=self.ffmpeg_dir_var, font=("Yu Gothic UI", 11)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Button(ffmpeg_row, text="设置", command=self._open_settings_window).grid(
            row=0, column=2, sticky="e", padx=(12, 0)
        )
        ttk.Label(
            ffmpeg_row,
            text="提示: FFmpeg 目录、输出命名等偏好设置可在“设置”窗口中调整并保存到本地。",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=1, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(6, 0))

        card_row = ttk.Frame(shell)
        card_row.grid(row=3, column=0, sticky="nsew")
        for index in range(3):
            card_row.columnconfigure(index, weight=1, uniform="dropzones")
        card_row.rowconfigure(0, weight=1)

        self.video_zone = DropZone(
            card_row,
            title="字幕视频",
            hint="支持 mkv / mp4 / mov / avi\n这里会决定输出文件名和输出目录。",
            extensions=VIDEO_EXTENSIONS,
            on_click=self._choose_video,
        )
        self.video_zone.frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.on_vocal_zone = DropZone(
            card_row,
            title="原唱无损",
            hint="支持 flac / wav / m4a / aac / ape / alac / mkv\n拖入原唱音频或含单音轨的 mkv。",
            extensions=AUDIO_EXTENSIONS,
            on_click=self._choose_on_audio,
        )
        self.on_vocal_zone.frame.grid(row=0, column=1, sticky="nsew", padx=5)

        self.off_vocal_zone = DropZone(
            card_row,
            title="伴奏无损",
            hint="支持 flac / wav / m4a / aac / ape / alac / mkv\n拖入伴奏音频或含单音轨的 mkv。",
            extensions=AUDIO_EXTENSIONS,
            on_click=self._choose_off_audio,
        )
        self.off_vocal_zone.frame.grid(row=0, column=2, sticky="nsew", padx=(10, 0))

        log_panel = tk.Frame(
            shell,
            bg="#ffffff",
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#d5dce6",
            padx=14,
            pady=14,
        )
        log_panel.grid(row=4, column=0, sticky="nsew", pady=(18, 0))
        log_panel.grid_columnconfigure(0, weight=1)
        log_panel.grid_rowconfigure(1, weight=1)

        tk.Label(
            log_panel,
            text="处理日志",
            bg="#ffffff",
            fg="#111827",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        self.log_text = tk.Text(
            log_panel,
            wrap="word",
            font=("Consolas", 10),
            relief="flat",
            bg="#ffffff",
            fg="#1f2937",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.configure(state="disabled")

        scrollbar = ttk.Scrollbar(log_panel, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        controls = ttk.Frame(shell)
        controls.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        controls.columnconfigure(2, weight=1)

        self.start_button = ttk.Button(controls, text="开始生成", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="w")

        ttk.Button(controls, text="打开输出目录", command=self._open_output_dir).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )

        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=2, sticky="e")

        ttk.Label(controls, textvariable=self.status_var, font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=3, sticky="e", padx=(12, 0)
        )

    def _build_alignment_ui(self, parent) -> None:
        shell = ttk.Frame(parent, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="音频波形对齐",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="把字幕视频和原唱音源放进来，选择要修正的对象，手动对齐波形后导出对应文件。",
            font=("Microsoft YaHei UI", 11),
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(header, text="设置 FFmpeg", command=self._open_settings_window).grid(
            row=0, column=1, sticky="e"
        )

        drop_row = ttk.Frame(shell)
        drop_row.grid(row=1, column=0, sticky="ew", pady=(14, 10))
        drop_row.columnconfigure(0, weight=1, uniform="align_dropzones")
        drop_row.columnconfigure(1, weight=1, uniform="align_dropzones")

        self.align_video_zone = DropZone(
            drop_row,
            title="字幕视频",
            hint="支持 mkv / mp4 / mov / avi\n用于读取原视频里的参考音轨。",
            extensions=VIDEO_EXTENSIONS,
            on_click=self._choose_align_video,
        )
        self.align_video_zone.frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.align_audio_zone = DropZone(
            drop_row,
            title="原唱音源",
            hint="支持 flac / wav / m4a / aac / ape / alac / mkv\n可作为固定参考，也可导出修正后的音频。",
            extensions=AUDIO_EXTENSIONS,
            on_click=self._choose_align_audio,
        )
        self.align_audio_zone.frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        actions = ttk.Frame(shell)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        actions.columnconfigure(3, weight=1)
        self.align_generate_button = ttk.Button(actions, text="生成波形", command=self._start_waveform_analysis)
        self.align_generate_button.grid(row=0, column=0, sticky="w")
        self.align_export_button = ttk.Button(actions, text="导出对齐视频", command=self._start_aligned_export)
        self.align_export_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        self.align_export_button.configure(state="disabled")
        ttk.Button(actions, text="打开输出目录", command=self._open_align_output_dir).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        self.align_progress = ttk.Progressbar(actions, mode="indeterminate", length=170)
        self.align_progress.grid(row=0, column=3, sticky="e")
        ttk.Label(actions, textvariable=self.align_status_var, font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=4, sticky="e", padx=(12, 0)
        )

        control_panel = tk.Frame(
            shell,
            bg="#ffffff",
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#d5dce6",
            padx=14,
            pady=8,
        )
        control_panel.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        control_panel.columnconfigure(1, weight=1)

        tk.Label(
            control_panel,
            textvariable=self.align_offset_var,
            bg="#ffffff",
            fg="#111827",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))

        target_row = ttk.Frame(control_panel)
        target_row.grid(row=0, column=1, sticky="ew")
        ttk.Label(target_row, text="对齐目标").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            target_row,
            text="调整字幕视频",
            variable=self.align_target_var,
            value=ALIGN_TARGET_VIDEO,
            command=self._handle_align_target_changed,
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Radiobutton(
            target_row,
            text="调整原唱音源",
            variable=self.align_target_var,
            value=ALIGN_TARGET_AUDIO,
            command=self._handle_align_target_changed,
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        mode_row = ttk.Frame(control_panel)
        mode_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(mode_row, text="拖动模式").grid(row=0, column=0, sticky="w")
        self.align_move_radio = ttk.Radiobutton(
            mode_row,
            text="移动字幕视频",
            variable=self.align_drag_mode_var,
            value="offset",
        )
        self.align_move_radio.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Radiobutton(
            mode_row,
            text="平移视图",
            variable=self.align_drag_mode_var,
            value="pan",
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        nudge_row = ttk.Frame(control_panel)
        nudge_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(nudge_row, text="-0.100s", command=lambda: self._nudge_align_offset(-0.1)).grid(
            row=0, column=0
        )
        ttk.Button(nudge_row, text="-0.010s", command=lambda: self._nudge_align_offset(-0.01)).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Button(nudge_row, text="+0.010s", command=lambda: self._nudge_align_offset(0.01)).grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Button(nudge_row, text="+0.100s", command=lambda: self._nudge_align_offset(0.1)).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Button(nudge_row, text="归零", command=self._reset_align_offset).grid(
            row=0, column=4, padx=(6, 0)
        )

        self.align_encode_row = ttk.Frame(control_panel)
        self.align_encode_row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(self.align_encode_row, text="补黑编码").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            self.align_encode_row,
            text="软编省空间",
            variable=self.align_encode_mode_var,
            value=ENCODE_MODE_SOFTWARE,
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Radiobutton(
            self.align_encode_row,
            text="硬编快速",
            variable=self.align_encode_mode_var,
            value=ENCODE_MODE_HARDWARE,
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))

        zoom_row = ttk.Frame(control_panel)
        zoom_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        zoom_row.columnconfigure(1, weight=1)
        ttk.Label(zoom_row, text="缩放").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            zoom_row,
            from_=8,
            to=800,
            variable=self.align_zoom_var,
            command=self._handle_align_zoom_change,
        ).grid(row=0, column=1, sticky="ew", padx=(12, 0))
        ttk.Button(zoom_row, text="回到开头", command=self._reset_align_view).grid(
            row=0, column=2, sticky="e", padx=(12, 0)
        )

        viewer_shell = ttk.Frame(shell)
        viewer_shell.grid(row=4, column=0, sticky="nsew")
        viewer_shell.columnconfigure(0, weight=1)
        viewer_shell.rowconfigure(0, weight=1)
        self.align_viewer = WaveformViewer(
            viewer_shell,
            mode_var=self.align_drag_mode_var,
            target_var=self.align_target_var,
            on_offset_changed=self._handle_align_offset_changed,
        )
        self.align_viewer.canvas.grid(row=0, column=0, sticky="nsew")

        log_panel = tk.Frame(
            shell,
            bg="#ffffff",
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#d5dce6",
            padx=14,
            pady=8,
        )
        log_panel.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        log_panel.columnconfigure(0, weight=1)
        tk.Label(
            log_panel,
            text="对齐日志",
            bg="#ffffff",
            fg="#111827",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.align_log_text = tk.Text(
            log_panel,
            height=3,
            wrap="word",
            font=("Consolas", 10),
            relief="flat",
            bg="#ffffff",
            fg="#1f2937",
        )
        self.align_log_text.grid(row=1, column=0, sticky="ew")
        self.align_log_text.configure(state="disabled")
        self._refresh_align_target_ui()

    def _update_output_template_state(self) -> None:
        state = "normal" if self.output_name_mode_var.get() == OUTPUT_NAME_MODE_TEMPLATE else "disabled"
        if self.on_template_entry is not None:
            self.on_template_entry.configure(state=state)
        if self.off_template_entry is not None:
            self.off_template_entry.configure(state=state)

    def _refresh_ffmpeg_display(self) -> None:
        if self.ffmpeg_display_label is None:
            return

        current = self.ffmpeg_dir_var.get().strip()
        is_placeholder = not current or current == FFMPEG_DIR_PLACEHOLDER
        self.ffmpeg_display_label.configure(
            fg="#6b7280" if is_placeholder else "#111827",
        )

    def _sync_settings_scrollbar(self) -> None:
        if (
            self.settings_canvas is None
            or self.settings_scrollbar is None
            or self.settings_content_frame is None
        ):
            return

        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        needs_scrollbar = self.settings_content_frame.winfo_reqheight() > self.settings_canvas.winfo_height()
        if needs_scrollbar:
            self.settings_scrollbar.grid()
        else:
            self.settings_scrollbar.grid_remove()
            self.settings_canvas.yview_moveto(0)

    def _handle_settings_content_configure(self, _event=None) -> None:
        self._sync_settings_scrollbar()

    def _handle_settings_canvas_configure(self, event) -> None:
        if self.settings_canvas is not None and self.settings_canvas_window_id is not None:
            self.settings_canvas.itemconfigure(self.settings_canvas_window_id, width=event.width)
        self._sync_settings_scrollbar()

    def _handle_settings_mousewheel(self, event) -> None:
        if self.settings_canvas is None or self.settings_scrollbar is None:
            return

        if not self.settings_scrollbar.winfo_ismapped():
            return

        delta = 0
        if getattr(event, "delta", 0):
            delta = -1 * int(event.delta / 120)
        elif getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1

        if delta:
            self.settings_canvas.yview_scroll(delta, "units")

    def _autosize_settings_window(self) -> None:
        if self.settings_window is None or self.settings_content_frame is None:
            return

        self.settings_window.update_idletasks()
        screen_width = self.settings_window.winfo_screenwidth()
        screen_height = self.settings_window.winfo_screenheight()
        target_width = min(max(self.settings_content_frame.winfo_reqwidth() + 60, 820), screen_width - 120)
        target_height = min(
            max(self.settings_content_frame.winfo_reqheight() + 40, 360),
            screen_height - 120,
        )
        self.settings_window.geometry(self._build_centered_geometry(target_width, target_height))
        self._sync_settings_scrollbar()

    def _open_settings_window(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        self.settings_status_var.set("")
        window = tk.Toplevel(self.root)
        window.title(f"{APP_TITLE} - 设置")
        window.minsize(700, 300)
        window.configure(bg="#eef2f7")
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_settings_window)
        window.bind("<MouseWheel>", self._handle_settings_mousewheel, add="+")
        window.bind("<Button-4>", self._handle_settings_mousewheel, add="+")
        window.bind("<Button-5>", self._handle_settings_mousewheel, add="+")

        outer = ttk.Frame(window)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(
            outer,
            bg="#eef2f7",
            highlightthickness=0,
            bd=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        scrollbar.grid_remove()
        canvas.configure(yscrollcommand=scrollbar.set)

        shell = ttk.Frame(canvas, padding=20)
        shell.columnconfigure(0, weight=1)
        canvas_window_id = canvas.create_window((0, 0), window=shell, anchor="nw")
        shell.bind("<Configure>", self._handle_settings_content_configure, add="+")
        canvas.bind("<Configure>", self._handle_settings_canvas_configure, add="+")

        ttk.Label(
            shell,
            text="设置",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ffmpeg_panel = ttk.Frame(shell, padding=(0, 18, 0, 0))
        ffmpeg_panel.grid(row=2, column=0, sticky="ew")
        ffmpeg_panel.columnconfigure(1, weight=1)

        ttk.Label(ffmpeg_panel, text="FFmpeg 目录", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="nw"
        )
        ffmpeg_content = ttk.Frame(ffmpeg_panel)
        ffmpeg_content.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        ffmpeg_content.columnconfigure(0, weight=1)

        self.ffmpeg_display_label = tk.Label(
            ffmpeg_content,
            textvariable=self.ffmpeg_dir_var,
            bg="#ffffff",
            fg="#6b7280",
            bd=1,
            relief="solid",
            padx=10,
            pady=8,
            anchor="w",
            justify="left",
            cursor="hand2",
        )
        self.ffmpeg_display_label.grid(row=0, column=0, sticky="ew")
        self.ffmpeg_display_label.bind("<Button-1>", lambda _event: self._choose_ffmpeg_dir(), add="+")
        ttk.Button(ffmpeg_content, text="选择目录", command=self._choose_ffmpeg_dir).grid(
            row=0, column=1, sticky="e", padx=(10, 0)
        )
        ttk.Button(ffmpeg_content, text="使用系统 PATH", command=self._use_system_ffmpeg).grid(
            row=0, column=2, sticky="e", padx=(10, 0)
        )
        ttk.Label(
            ffmpeg_content,
            text="推荐直接选择 ffmpeg 的 bin 目录，例如 D:\\tools\\ffmpeg\\bin。",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(
            ffmpeg_content,
            text="也可以选择 ffmpeg 根目录，程序会自动尝试其中的 bin\\ffmpeg.exe 和 bin\\ffprobe.exe。",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        naming_panel = ttk.Frame(shell, padding=(0, 18, 0, 0))
        naming_panel.grid(row=3, column=0, sticky="ew")
        naming_panel.columnconfigure(1, weight=1)

        ttk.Label(naming_panel, text="输出命名", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="nw"
        )

        naming_content = ttk.Frame(naming_panel)
        naming_content.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        naming_content.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            naming_content,
            text=OUTPUT_NAME_MODE_LABELS[OUTPUT_NAME_MODE_FIXED],
            variable=self.output_name_mode_var,
            value=OUTPUT_NAME_MODE_FIXED,
            command=self._update_output_template_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(
            naming_content,
            text=OUTPUT_NAME_MODE_LABELS[OUTPUT_NAME_MODE_TEMPLATE],
            variable=self.output_name_mode_var,
            value=OUTPUT_NAME_MODE_TEMPLATE,
            command=self._update_output_template_state,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(naming_content, text="原唱模板").grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.on_template_entry = ttk.Entry(naming_content, textvariable=self.on_name_template_var)
        self.on_template_entry.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=(12, 0))

        ttk.Label(naming_content, text="伴奏模板").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.off_template_entry = ttk.Entry(naming_content, textvariable=self.off_name_template_var)
        self.off_template_entry.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))

        ttk.Label(
            naming_content,
            text="支持占位符: {video_name}。不需要写 .mkv。示例: {video_name}_karaoke_on",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(
            naming_content,
            text="保存后，下次启动软件会自动加载这套命名设置。",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(
            shell,
            textvariable=self.settings_status_var,
            font=("Microsoft YaHei UI", 9),
            foreground="#177245",
        ).grid(row=4, column=0, sticky="w", pady=(16, 0))

        controls = ttk.Frame(shell)
        controls.grid(row=5, column=0, sticky="e", pady=(16, 0))

        ttk.Button(controls, text="保存设置", command=self._save_settings).grid(
            row=0, column=0, sticky="e"
        )
        ttk.Button(controls, text="关闭", command=self._close_settings_window).grid(
            row=0, column=1, sticky="e", padx=(10, 0)
        )

        self.settings_window = window
        self.settings_canvas = canvas
        self.settings_scrollbar = scrollbar
        self.settings_content_frame = shell
        self.settings_canvas_window_id = canvas_window_id
        self._update_output_template_state()
        self._refresh_ffmpeg_display()
        self._autosize_settings_window()

    def _close_settings_window(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.settings_window = None
        self.settings_canvas = None
        self.settings_scrollbar = None
        self.settings_content_frame = None
        self.settings_canvas_window_id = None
        self.ffmpeg_display_label = None
        self.on_template_entry = None
        self.off_template_entry = None

    def _install_file_drop(self) -> None:
        self.drop_handler = WindowsFileDropHandler(self.root, self._handle_drop)
        self.drop_handler.install()

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def _append_align_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.align_log_queue.put(f"[{timestamp}] {message}")

    def _drain_log_queue(self) -> None:
        drained = False
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        while True:
            try:
                line = self.align_log_queue.get_nowait()
            except queue.Empty:
                break
            if self.align_log_text is None:
                continue
            drained = True
            self.align_log_text.configure(state="normal")
            self.align_log_text.insert("end", line + "\n")
            self.align_log_text.see("end")
            self.align_log_text.configure(state="disabled")
        if drained:
            self.root.update_idletasks()
        self.root.after(100, self._drain_log_queue)

    def set_video_path(self, path: Path) -> None:
        self.video_var.set(str(path))
        self.video_zone.set_path(path)
        self.output_dir_var.set(str(resolve_output_dir(path)))

    def set_on_vocal_path(self, path: Path) -> None:
        self.on_vocal_var.set(str(path))
        self.on_vocal_zone.set_path(path)

    def set_off_vocal_path(self, path: Path) -> None:
        self.off_vocal_var.set(str(path))
        self.off_vocal_zone.set_path(path)

    def set_align_video_path(self, path: Path) -> None:
        self.align_video_var.set(str(path))
        self.align_video_zone.set_path(path)
        self._invalidate_alignment_waveforms()

    def set_align_audio_path(self, path: Path) -> None:
        self.align_audio_var.set(str(path))
        self.align_audio_zone.set_path(path)
        self._invalidate_alignment_waveforms()

    def set_ffmpeg_dir(self, path: Path) -> None:
        self.ffmpeg_dir_var.set(str(path))
        self._refresh_ffmpeg_display()

    def set_output_name_mode(self, mode: str) -> None:
        if mode == OUTPUT_NAME_MODE_VIDEO_NAME:
            mode = OUTPUT_NAME_MODE_TEMPLATE
            self.set_output_name_templates(DEFAULT_ON_NAME_TEMPLATE, DEFAULT_OFF_NAME_TEMPLATE)
        if mode not in OUTPUT_NAME_MODE_LABELS:
            raise ProcessingError(f"不支持的输出命名模式: {mode}")
        self.output_name_mode_var.set(mode)
        self._update_output_template_state()

    def set_output_name_templates(self, on_template: str, off_template: str) -> None:
        self.on_name_template_var.set(on_template)
        self.off_name_template_var.set(off_template)

    def _resolve_output_name_mode(self) -> str:
        output_name_mode = self.output_name_mode_var.get().strip()
        if output_name_mode not in OUTPUT_NAME_MODE_LABELS:
            raise ProcessingError("输出命名模式无效，请重新选择。")
        return output_name_mode

    def _resolve_output_name_templates(self, *, require_valid: bool) -> tuple[str, str]:
        on_template = self.on_name_template_var.get().strip() or DEFAULT_ON_NAME_TEMPLATE
        off_template = self.off_name_template_var.get().strip() or DEFAULT_OFF_NAME_TEMPLATE
        if require_valid:
            on_template = validate_output_name_template(on_template, "原唱")
            off_template = validate_output_name_template(off_template, "伴奏")
        return on_template, off_template

    def _save_settings(self) -> None:
        try:
            output_name_mode = self._resolve_output_name_mode()
            if output_name_mode == OUTPUT_NAME_MODE_TEMPLATE:
                on_template, off_template = self._resolve_output_name_templates(require_valid=True)
            else:
                on_template, off_template = self._resolve_output_name_templates(require_valid=False)
            resolved_ffmpeg_dir = self._resolve_ffmpeg_dir()
            ffmpeg_dir = str(resolved_ffmpeg_dir) if resolved_ffmpeg_dir else ""

            saved_path = save_app_settings(
                AppSettings(
                    output_name_mode=output_name_mode,
                    on_name_template=on_template,
                    off_name_template=off_template,
                    ffmpeg_dir=ffmpeg_dir,
                )
            )
        except ProcessingError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.settings_status_var.set("设置已保存到本地。")
        messagebox.showinfo(APP_TITLE, f"设置已保存。\n{saved_path}")

    def _choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择字幕视频",
            filetypes=VIDEO_FILETYPES,
            initialdir=self._current_browse_dir(),
        )
        if path:
            self.set_video_path(Path(path))

    def _choose_on_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择原唱无损音频",
            filetypes=AUDIO_FILETYPES,
            initialdir=self._current_browse_dir(),
        )
        if path:
            self.set_on_vocal_path(Path(path))

    def _choose_off_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择伴奏无损音频",
            filetypes=AUDIO_FILETYPES,
            initialdir=self._current_browse_dir(),
        )
        if path:
            self.set_off_vocal_path(Path(path))

    def _choose_align_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择用于对齐的字幕视频",
            filetypes=VIDEO_FILETYPES,
            initialdir=self._current_align_browse_dir(),
        )
        if path:
            self.set_align_video_path(Path(path))

    def _choose_align_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择需要对齐的原唱音源",
            filetypes=AUDIO_FILETYPES,
            initialdir=self._current_align_browse_dir(),
        )
        if path:
            self.set_align_audio_path(Path(path))

    def _choose_ffmpeg_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 ffmpeg 所在目录")
        if path:
            self.set_ffmpeg_dir(Path(path))
            self.settings_status_var.set("已选择 FFmpeg 目录。点击“保存设置”后会保存到本地。")

    def _use_system_ffmpeg(self) -> None:
        self.ffmpeg_dir_var.set(FFMPEG_DIR_PLACEHOLDER)
        self._refresh_ffmpeg_display()
        self.settings_status_var.set("已切换为使用系统 PATH。点击“保存设置”后会保存这个选择。")

    def _current_browse_dir(self) -> str | None:
        video_path = self.video_var.get().strip()
        if video_path:
            return str(Path(video_path).expanduser().parent)
        return None

    def _current_align_browse_dir(self) -> str | None:
        for raw_path in (self.align_video_var.get().strip(), self.align_audio_var.get().strip()):
            if raw_path:
                return str(Path(raw_path).expanduser().parent)
        return self._current_browse_dir()

    def _handle_drop(self, raw_paths: list[str], screen_x: int, screen_y: int) -> None:
        if not raw_paths:
            return

        widget = self.root.winfo_containing(screen_x, screen_y)
        if widget is None:
            return

        zone = self._zone_for_widget(widget)
        if zone is None:
            return

        path = Path(raw_paths[0]).expanduser()
        if not zone.accepts(path):
            messagebox.showerror(APP_TITLE, f"这个区域不接受该文件:\n{path}")
            return

        if zone is self.video_zone:
            self.set_video_path(path)
        elif zone is self.on_vocal_zone:
            self.set_on_vocal_path(path)
        elif zone is self.off_vocal_zone:
            self.set_off_vocal_path(path)
        elif zone is self.align_video_zone:
            self.set_align_video_path(path)
        elif zone is self.align_audio_zone:
            self.set_align_audio_path(path)

    def _zone_for_widget(self, widget):
        zones = (
            self.video_zone,
            self.on_vocal_zone,
            self.off_vocal_zone,
            self.align_video_zone,
            self.align_audio_zone,
        )
        for zone in zones:
            if zone.contains_widget(widget):
                return zone
        return None

    def _handle_align_offset_changed(self, seconds: float) -> None:
        label = "字幕视频偏移" if self._is_align_video_target() else "原唱音源偏移"
        self.align_offset_var.set(f"{label} {format_offset(seconds)}")

    def _is_align_video_target(self) -> bool:
        return self.align_target_var.get() == ALIGN_TARGET_VIDEO

    def _handle_align_target_changed(self) -> None:
        if self.align_viewer is not None:
            self.align_viewer.set_offset(0.0)
            self.align_viewer.draw()
        self._refresh_align_target_ui()

    def _refresh_align_target_ui(self) -> None:
        is_video_target = self._is_align_video_target()
        self._handle_align_offset_changed(self.align_viewer.offset_seconds if self.align_viewer else 0.0)
        if self.align_move_radio is not None:
            self.align_move_radio.configure(text="移动字幕视频" if is_video_target else "移动原唱音源")
        if hasattr(self, "align_export_button"):
            self.align_export_button.configure(
                text="导出对齐视频" if is_video_target else "导出对齐音频"
            )
        if self.align_encode_row is not None:
            state = "normal" if is_video_target else "disabled"
            for child in self.align_encode_row.winfo_children():
                try:
                    child.configure(state=state)
                except tk.TclError:
                    pass

    def _invalidate_alignment_waveforms(self) -> None:
        if self.align_viewer is not None:
            self.align_viewer.clear()
        if hasattr(self, "align_export_button"):
            self.align_export_button.configure(state="disabled")
        self.align_status_var.set("准备生成波形")
        self._refresh_align_target_ui()

    def _handle_align_zoom_change(self, value: str) -> None:
        if self.align_viewer is None:
            return
        try:
            zoom = float(value)
        except ValueError:
            return
        self.align_viewer.set_zoom(zoom)

    def _nudge_align_offset(self, delta_seconds: float) -> None:
        if self.align_viewer is not None:
            self.align_viewer.nudge_offset(delta_seconds)

    def _reset_align_offset(self) -> None:
        if self.align_viewer is not None:
            self.align_viewer.set_offset(0.0)

    def _reset_align_view(self) -> None:
        if self.align_viewer is not None:
            self.align_viewer.reset_view()

    def _validate_alignment_inputs(self) -> tuple[Path, Path]:
        video_path = Path(self.align_video_var.get()).expanduser()
        audio_path = Path(self.align_audio_var.get()).expanduser()
        missing = [
            label
            for label, path in [
                ("字幕视频", video_path),
                ("原唱音源", audio_path),
            ]
            if not path.is_file()
        ]
        if missing:
            raise ProcessingError(f"请先选择有效的文件: {', '.join(missing)}")
        return video_path, audio_path

    def _start_waveform_analysis(self) -> None:
        if self.align_worker and self.align_worker.is_alive():
            messagebox.showinfo(APP_TITLE, "当前波形任务还在处理，请稍等。")
            return

        try:
            video_path, audio_path = self._validate_alignment_inputs()
            ffmpeg_dir = self._resolve_ffmpeg_dir()
        except ProcessingError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if self.align_log_text is not None:
            self.align_log_text.configure(state="normal")
            self.align_log_text.delete("1.0", "end")
            self.align_log_text.configure(state="disabled")

        self.align_generate_button.configure(state="disabled")
        self.align_export_button.configure(state="disabled")
        self.align_progress.start(10)
        self.align_status_var.set("生成波形中...")

        def worker() -> None:
            try:
                video_waveform = extract_waveform(
                    video_path,
                    ffmpeg_dir,
                    self._append_align_log,
                    label="字幕视频音轨",
                )
                audio_waveform = extract_waveform(
                    audio_path,
                    ffmpeg_dir,
                    self._append_align_log,
                    label="原唱音源",
                )
            except Exception as exc:  # noqa: BLE001
                self._append_align_log(f"波形生成失败: {exc}")
                self.root.after(0, lambda: self._finish_waveform_analysis(False, str(exc), None, None))
                return

            self.root.after(
                0,
                lambda: self._finish_waveform_analysis(True, "", video_waveform, audio_waveform),
            )

        self.align_worker = threading.Thread(target=worker, daemon=True)
        self.align_worker.start()

    def _finish_waveform_analysis(
        self,
        success: bool,
        message: str,
        video_waveform: WaveformData | None,
        audio_waveform: WaveformData | None,
    ) -> None:
        self.align_progress.stop()
        self.align_generate_button.configure(state="normal")
        self.align_export_button.configure(state="normal" if success else "disabled")
        self.align_status_var.set("波形已生成" if success else "波形生成失败")

        if success:
            if self.align_viewer is not None:
                self.align_viewer.set_waveforms(
                    video_waveform=video_waveform,
                    audio_waveform=audio_waveform,
                )
                self._refresh_align_target_ui()
            return

        messagebox.showerror(APP_TITLE, message)

    def _start_aligned_export(self) -> None:
        if self.align_export_worker and self.align_export_worker.is_alive():
            messagebox.showinfo(APP_TITLE, "当前导出任务还在处理，请稍等。")
            return

        try:
            video_path, audio_path = self._validate_alignment_inputs()
            ffmpeg_dir = self._resolve_ffmpeg_dir()
        except ProcessingError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        if self.align_viewer is None or self.align_viewer.video_waveform is None or self.align_viewer.audio_waveform is None:
            messagebox.showerror(APP_TITLE, "请先生成波形并完成对齐。")
            return

        is_video_target = self._is_align_video_target()
        output_kind = "对齐视频" if is_video_target else "对齐音频"
        initial_path = (
            default_aligned_video_path(video_path)
            if is_video_target
            else default_aligned_audio_path(audio_path)
        )
        output_path_raw = filedialog.asksaveasfilename(
            title="导出对齐视频" if is_video_target else "导出对齐音频",
            initialdir=str(initial_path.parent),
            initialfile=initial_path.name,
            defaultextension=".mkv" if is_video_target else initial_path.suffix,
            filetypes=(
                [("Matroska 视频", "*.mkv"), ("所有文件", "*.*")]
                if is_video_target
                else [("WAV 音频", "*.wav"), ("所有文件", "*.*")]
            ),
        )
        if not output_path_raw:
            return

        output_path = Path(output_path_raw).expanduser()
        offset_seconds = self.align_viewer.offset_seconds
        encode_mode = self.align_encode_mode_var.get()
        self.align_generate_button.configure(state="disabled")
        self.align_export_button.configure(state="disabled")
        self.align_progress.start(10)
        self.align_status_var.set("导出对齐视频中..." if is_video_target else "导出对齐音频中...")

        def worker() -> None:
            try:
                if is_video_target:
                    output = export_aligned_video(
                        video_path=video_path,
                        output_path=output_path,
                        offset_seconds=offset_seconds,
                        ffmpeg_dir=ffmpeg_dir,
                        logger=self._append_align_log,
                        encode_mode=encode_mode,
                    )
                else:
                    output = export_aligned_audio(
                        audio_path=audio_path,
                        output_path=output_path,
                        offset_seconds=offset_seconds,
                        ffmpeg_dir=ffmpeg_dir,
                        logger=self._append_align_log,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_align_log(f"导出失败: {exc}")
                self.root.after(0, lambda: self._finish_aligned_export(False, str(exc), None, output_kind))
                return

            self.root.after(0, lambda: self._finish_aligned_export(True, "", output, output_kind))

        self.align_export_worker = threading.Thread(target=worker, daemon=True)
        self.align_export_worker.start()

    def _finish_aligned_export(
        self,
        success: bool,
        message: str,
        output_path: Path | None,
        output_kind: str,
    ) -> None:
        self.align_progress.stop()
        self.align_generate_button.configure(state="normal")
        self.align_export_button.configure(state="normal")
        self.align_status_var.set("导出完成" if success else "导出失败")

        if success and output_path is not None:
            messagebox.showinfo(APP_TITLE, f"{output_kind}已导出:\n{output_path}")
            return

        messagebox.showerror(APP_TITLE, message)

    def _open_align_output_dir(self) -> None:
        raw_path = self.align_audio_var.get().strip() or self.align_video_var.get().strip()
        if not raw_path:
            messagebox.showinfo(APP_TITLE, "请先选择文件。")
            return

        output_dir = Path(raw_path).expanduser().parent
        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(output_dir)])

    def _resolve_output_dir(self) -> Path:
        video_path = self.video_var.get().strip()
        if not video_path:
            raise ProcessingError("请先选择字幕视频。")
        return resolve_output_dir(Path(video_path).expanduser())

    def _resolve_ffmpeg_dir(self) -> Path | None:
        ffmpeg_dir = self.ffmpeg_dir_var.get().strip()
        if not ffmpeg_dir or ffmpeg_dir == FFMPEG_DIR_PLACEHOLDER:
            return None

        path = Path(ffmpeg_dir).expanduser()
        if not path.is_dir():
            raise ProcessingError("所选 ffmpeg 目录无效，请重新选择。")
        return path

    def _open_output_dir(self) -> None:
        try:
            output_dir = self._resolve_output_dir()
        except ProcessingError as exc:
            messagebox.showinfo(APP_TITLE, str(exc))
            return

        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(output_dir)])

    def _validate_inputs(self) -> tuple[Path, Path, Path, Path, str, str | None, str | None]:
        video_path = Path(self.video_var.get()).expanduser()
        on_vocal_path = Path(self.on_vocal_var.get()).expanduser()
        off_vocal_path = Path(self.off_vocal_var.get()).expanduser()
        output_dir = self._resolve_output_dir()
        output_name_mode = self._resolve_output_name_mode()
        self._resolve_ffmpeg_dir()

        missing = [
            label
            for label, path in [
                ("字幕视频", video_path),
                ("原唱无损", on_vocal_path),
                ("伴奏无损", off_vocal_path),
            ]
            if not path.is_file()
        ]
        if missing:
            raise ProcessingError(f"请先选择有效的文件: {', '.join(missing)}")

        if on_vocal_path.resolve() == off_vocal_path.resolve():
            raise ProcessingError("原唱无损和伴奏无损不能是同一个文件。")

        if output_name_mode == OUTPUT_NAME_MODE_TEMPLATE:
            on_template, off_template = self._resolve_output_name_templates(require_valid=True)
        else:
            on_template, off_template = None, None

        return (
            video_path,
            on_vocal_path,
            off_vocal_path,
            output_dir,
            output_name_mode,
            on_template,
            off_template,
        )

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "当前任务还在处理中，请稍等。")
            return

        try:
            (
                video_path,
                on_vocal_path,
                off_vocal_path,
                output_dir,
                output_name_mode,
                on_name_template,
                off_name_template,
            ) = self._validate_inputs()
        except ProcessingError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.start_button.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("处理中...")

        def worker() -> None:
            try:
                ffmpeg_dir = self._resolve_ffmpeg_dir()
                outputs = run_pipeline(
                    video_path=video_path,
                    on_vocal_path=on_vocal_path,
                    off_vocal_path=off_vocal_path,
                    output_dir=output_dir,
                    ffmpeg_dir=ffmpeg_dir,
                    output_name_mode=output_name_mode,
                    on_name_template=on_name_template,
                    off_name_template=off_name_template,
                    logger=self._append_log,
                )
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"处理失败: {exc}")
                self.root.after(0, lambda: self._finish(False, str(exc)))
                return

            output_lines = "\n".join(str(path) for path in outputs)
            self.root.after(0, lambda: self._finish(True, output_lines))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _finish(self, success: bool, message: str) -> None:
        self.progress.stop()
        self.start_button.configure(state="normal")
        self.status_var.set("完成" if success else "失败")

        if success:
            messagebox.showinfo(APP_TITLE, f"输出完成:\n{message}")
        else:
            messagebox.showerror(APP_TITLE, message)
