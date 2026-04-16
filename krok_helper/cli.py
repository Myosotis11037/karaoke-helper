from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tkinter as tk
try:
    from tkinterdnd2 import Tk as DnDTk
except Exception:  # noqa: BLE001
    DnDTk = None

from krok_helper.errors import ProcessingError
from krok_helper.gui import KaraokeHiresApp
from krok_helper.pipeline import (
    DEFAULT_OFF_NAME_TEMPLATE,
    DEFAULT_ON_NAME_TEMPLATE,
    OUTPUT_NAME_MODE_FIXED,
    OUTPUT_NAME_MODE_TEMPLATE,
    OUTPUT_NAME_MODE_VIDEO_NAME,
    run_pipeline,
)
from krok_helper.settings import load_app_settings
from krok_helper.windows import apply_tk_scaling, enable_high_dpi_awareness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="卡拉 OK 字幕视频一键 Hi-Res 生成工具")
    parser.add_argument("--video", type=Path, help="字幕视频路径")
    parser.add_argument("--on-audio", type=Path, help="原唱无损音频路径")
    parser.add_argument("--off-audio", type=Path, help="伴奏无损音频路径")
    parser.add_argument("--output-dir", type=Path, help="输出目录，可选，默认使用字幕视频所在目录")
    parser.add_argument(
        "--ffmpeg-dir",
        type=Path,
        help="ffmpeg 所在目录，可选。系统 PATH 优先，找不到时再回退到这里。",
    )
    parser.add_argument(
        "--output-name-mode",
        choices=[OUTPUT_NAME_MODE_FIXED, OUTPUT_NAME_MODE_TEMPLATE, OUTPUT_NAME_MODE_VIDEO_NAME],
        help="输出文件命名模式，可选 fixed、template 或 video_name。",
    )
    parser.add_argument("--on-name-template", help="原唱输出模板，支持 {video_name}，不需要写 .mkv")
    parser.add_argument("--off-name-template", help="伴奏输出模板，支持 {video_name}，不需要写 .mkv")
    parser.add_argument("--gui", action="store_true", help="强制启动图形界面")
    return parser.parse_args()


def run_cli(args: argparse.Namespace) -> int:
    required = [args.video, args.on_audio, args.off_audio]
    if any(value is None for value in required):
        raise ProcessingError("命令行模式需要同时提供 --video、--on-audio 和 --off-audio。")

    saved_settings = load_app_settings()
    output_name_mode = args.output_name_mode or saved_settings.output_name_mode
    on_name_template = args.on_name_template or saved_settings.on_name_template or DEFAULT_ON_NAME_TEMPLATE
    off_name_template = (
        args.off_name_template or saved_settings.off_name_template or DEFAULT_OFF_NAME_TEMPLATE
    )
    ffmpeg_dir = args.ffmpeg_dir.expanduser() if args.ffmpeg_dir else None
    if ffmpeg_dir is None and saved_settings.ffmpeg_dir.strip():
        ffmpeg_dir = Path(saved_settings.ffmpeg_dir).expanduser()

    def logger(message: str) -> None:
        print(message)

    outputs = run_pipeline(
        video_path=args.video.expanduser(),
        on_vocal_path=args.on_audio.expanduser(),
        off_vocal_path=args.off_audio.expanduser(),
        output_dir=args.output_dir.expanduser() if args.output_dir else None,
        ffmpeg_dir=ffmpeg_dir,
        output_name_mode=output_name_mode,
        on_name_template=on_name_template,
        off_name_template=off_name_template,
        logger=logger,
    )
    print("输出文件:")
    for output in outputs:
        print(output)
    return 0


def run_gui(args: argparse.Namespace) -> int:
    enable_high_dpi_awareness()
    root = DnDTk() if DnDTk is not None else tk.Tk()
    apply_tk_scaling(root)
    app = KaraokeHiresApp(root)
    if args.video:
        app.set_video_path(args.video.expanduser())
    if args.on_audio:
        app.set_on_vocal_path(args.on_audio.expanduser())
    if args.off_audio:
        app.set_off_vocal_path(args.off_audio.expanduser())
    if args.ffmpeg_dir:
        app.set_ffmpeg_dir(args.ffmpeg_dir.expanduser())
    if args.output_name_mode:
        app.set_output_name_mode(args.output_name_mode)
    if args.on_name_template or args.off_name_template:
        app.set_output_name_templates(
            args.on_name_template or DEFAULT_ON_NAME_TEMPLATE,
            args.off_name_template or DEFAULT_OFF_NAME_TEMPLATE,
        )
    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    cli_requested = all(value is not None for value in [args.video, args.on_audio, args.off_audio])

    if cli_requested and not args.gui:
        try:
            return run_cli(args)
        except ProcessingError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    return run_gui(args)
