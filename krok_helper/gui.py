from __future__ import annotations

import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
            padx=18,
            pady=18,
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
        self.hint_label.pack(fill="x", pady=(10, 16))

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
        self.action_label.pack(fill="x", pady=(16, 0))

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

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
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

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1)

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
        else:
            self.set_off_vocal_path(path)

    def _zone_for_widget(self, widget):
        for zone in (self.video_zone, self.on_vocal_zone, self.off_vocal_zone):
            if zone.contains_widget(widget):
                return zone
        return None

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
