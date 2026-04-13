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
from krok_helper.pipeline import resolve_output_dir, run_pipeline
from krok_helper.windows import WindowsFileDropHandler


VIDEO_FILETYPES = [("视频文件", "*.mkv *.mp4 *.mov *.avi"), ("所有文件", "*.*")]
AUDIO_FILETYPES = [
    ("音频文件", "*.flac *.wav *.m4a *.aac *.ape *.alac *.mkv"),
    ("所有文件", "*.*"),
]
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".avi"}
AUDIO_EXTENSIONS = {".flac", ".wav", ".m4a", ".aac", ".ape", ".alac", ".mkv"}


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
            text="点击选择文件，或直接拖进这个框",
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
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.root.configure(bg="#eef2f7")

        self.video_var = tk.StringVar()
        self.on_vocal_var = tk.StringVar()
        self.off_vocal_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value="跟随字幕视频所在目录")
        self.status_var = tk.StringVar(value="准备就绪")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.drop_handler: WindowsFileDropHandler | None = None

        self._configure_styles()
        self._build_ui()
        self.root.after(100, self._drain_log_queue)
        self._install_file_drop()

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
        style.configure("TProgressbar", thickness=10)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(
            header,
            text="卡拉 OK 字幕视频一键 Hi-Res 生成",
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            header,
            text="把三个文件拖进下方卡片，或点击卡片选择文件。输出目录会自动使用字幕视频所在目录。",
            font=("Microsoft YaHei UI", 11),
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(8, 0))

        output_row = ttk.Frame(shell)
        output_row.grid(row=1, column=0, sticky="ew", pady=(18, 14))
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="输出目录", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(output_row, textvariable=self.output_dir_var, font=("Consolas", 10)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )

        card_row = ttk.Frame(shell)
        card_row.grid(row=2, column=0, sticky="nsew")
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
        log_panel.grid(row=3, column=0, sticky="nsew", pady=(18, 0))
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
        controls.grid(row=4, column=0, sticky="ew", pady=(18, 0))
        controls.columnconfigure(2, weight=1)

        self.start_button = ttk.Button(controls, text="开始生成", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="w")

        open_output = ttk.Button(controls, text="打开输出目录", command=self._open_output_dir)
        open_output.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=2, sticky="e")

        status = ttk.Label(controls, textvariable=self.status_var, font=("Microsoft YaHei UI", 10, "bold"))
        status.grid(row=0, column=3, sticky="e", padx=(12, 0))

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

    def _open_output_dir(self) -> None:
        try:
            output_dir = self._resolve_output_dir()
        except ProcessingError as exc:
            messagebox.showinfo(APP_TITLE, str(exc))
            return

        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(output_dir)])

    def _validate_inputs(self) -> tuple[Path, Path, Path, Path]:
        video_path = Path(self.video_var.get()).expanduser()
        on_vocal_path = Path(self.on_vocal_var.get()).expanduser()
        off_vocal_path = Path(self.off_vocal_var.get()).expanduser()
        output_dir = self._resolve_output_dir()

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

        return video_path, on_vocal_path, off_vocal_path, output_dir

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "当前任务还在处理中，请稍等。")
            return

        try:
            video_path, on_vocal_path, off_vocal_path, output_dir = self._validate_inputs()
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
                outputs = run_pipeline(
                    video_path=video_path,
                    on_vocal_path=on_vocal_path,
                    off_vocal_path=off_vocal_path,
                    output_dir=output_dir,
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
