from __future__ import annotations

import queue
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from krok_helper.config import APP_TITLE, DEFAULT_OUTPUT_DIR
from krok_helper.errors import ProcessingError
from krok_helper.pipeline import run_pipeline


class KaraokeHiresApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("840x620")
        self.root.minsize(760, 560)

        self.video_var = tk.StringVar()
        self.on_vocal_var = tk.StringVar()
        self.off_vocal_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.status_var = tk.StringVar(value="准备就绪")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)

        title = ttk.Label(
            frame,
            text="卡拉 OK 字幕视频一键 Hi-Res 生成",
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        title.grid(row=0, column=0, columnspan=3, sticky="w")

        subtitle = ttk.Label(
            frame,
            text="选择字幕视频、原唱无损和伴奏无损后，直接输出 on/off vocal 两个 Hi-Res MKV。",
        )
        subtitle.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 16))

        self._build_file_row(frame, 2, "字幕视频", self.video_var, self._choose_video)
        self._build_file_row(frame, 3, "原唱无损", self.on_vocal_var, self._choose_on_audio)
        self._build_file_row(frame, 4, "伴奏无损", self.off_vocal_var, self._choose_off_audio)
        self._build_file_row(frame, 5, "输出目录", self.output_var, self._choose_output_dir, select_file=False)

        log_label = ttk.Label(frame, text="处理日志")
        log_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(16, 6))

        self.log_text = tk.Text(frame, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.log_text.configure(state="disabled")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=7, column=3, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        controls = ttk.Frame(frame)
        controls.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        controls.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(controls, text="开始生成", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="w")

        open_output = ttk.Button(controls, text="打开输出目录", command=self._open_output_dir)
        open_output.grid(row=0, column=1, sticky="w", padx=(8, 0))

        status = ttk.Label(controls, textvariable=self.status_var)
        status.grid(row=0, column=2, sticky="e")

        self.progress = ttk.Progressbar(controls, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=3, sticky="e", padx=(12, 0))

    def _build_file_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command,
        *,
        select_file: bool = True,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(12, 8), pady=4)
        button_text = "选择文件" if select_file else "选择目录"
        ttk.Button(parent, text=button_text, command=command).grid(row=row, column=2, sticky="ew", pady=4)

    def _choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择字幕视频",
            filetypes=[("视频文件", "*.mkv *.mp4 *.mov *.avi"), ("所有文件", "*.*")],
        )
        if path:
            self.video_var.set(path)

    def _choose_on_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择原唱无损音频",
            filetypes=[("音频文件", "*.flac *.wav *.m4a *.aac *.ape *.alac *.mkv"), ("所有文件", "*.*")],
        )
        if path:
            self.on_vocal_var.set(path)

    def _choose_off_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择伴奏无损音频",
            filetypes=[("音频文件", "*.flac *.wav *.m4a *.aac *.ape *.alac *.mkv"), ("所有文件", "*.*")],
        )
        if path:
            self.off_vocal_var.set(path)

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

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

    def _open_output_dir(self) -> None:
        output_dir = Path(self.output_var.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(output_dir)])

    def _validate_inputs(self) -> tuple[Path, Path, Path, Path]:
        video_path = Path(self.video_var.get()).expanduser()
        on_vocal_path = Path(self.on_vocal_var.get()).expanduser()
        off_vocal_path = Path(self.off_vocal_var.get()).expanduser()
        output_dir = Path(self.output_var.get()).expanduser()

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
