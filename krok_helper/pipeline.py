from __future__ import annotations

from pathlib import Path

from krok_helper.config import DURATION_WARNING_SECONDS, MIN_HIRES_SAMPLE_RATE
from krok_helper.errors import ProcessingError
from krok_helper.ffmpeg import find_tool, probe_media, run_command
from krok_helper.models import MediaInfo
from krok_helper.types import Logger


def format_duration(seconds: float) -> str:
    seconds = max(0, seconds)
    whole = int(seconds)
    milliseconds = int(round((seconds - whole) * 1000))
    minutes, sec = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{sec:02d}.{milliseconds:03d}"


def log_media_summary(logger: Logger, label: str, info: MediaInfo) -> None:
    parts = [
        f"{label}: {info.path.name}",
        f"时长 {format_duration(info.duration)}",
        f"视频流 {info.video_streams}",
        f"音频流 {info.audio_streams}",
        f"字幕流 {info.subtitle_streams}",
    ]
    if info.sample_rate:
        parts.append(f"采样率 {info.sample_rate}Hz")
    if info.channels:
        parts.append(f"声道 {info.channels}")
    logger(" | ".join(parts))


def warn_duration_mismatch(
    logger: Logger,
    video_info: MediaInfo,
    audio_info: MediaInfo,
    label: str,
) -> None:
    delta = abs(video_info.duration - audio_info.duration)
    if delta > DURATION_WARNING_SECONDS:
        logger(
            f"警告: {label} 与字幕视频的时长相差 {delta:.2f} 秒，"
            "程序会继续处理，但建议你确认素材是否对齐。"
        )


def build_ffmpeg_command(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    audio_title: str,
    sample_rate: int,
) -> list[str]:
    return [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0",
        "-map",
        "-0:a",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:s",
        "copy",
        "-c:d",
        "copy",
        "-c:t",
        "copy",
        "-c:a",
        "flac",
        "-compression_level",
        "12",
        "-strict",
        "experimental",
        "-ar",
        str(sample_rate),
        "-sample_fmt",
        "s32",
        "-ac",
        "2",
        "-disposition:a:0",
        "default",
        "-map_metadata",
        "-1",
        "-metadata:s:a:0",
        f"title={audio_title}",
        str(output_path),
    ]


def process_output(
    ffmpeg_path: str,
    logger: Logger,
    video_info: MediaInfo,
    audio_info: MediaInfo,
    output_path: Path,
    label: str,
) -> Path:
    target_sample_rate = max(audio_info.sample_rate or 0, MIN_HIRES_SAMPLE_RATE)
    logger(f"开始生成 {label}: 目标音频 Hi-Res FLAC 32bit / {target_sample_rate}Hz / 2ch")

    command = build_ffmpeg_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_info.path,
        audio_path=audio_info.path,
        output_path=output_path,
        audio_title=f"Hi-Res Audio ({label}, FLAC 32bit/{target_sample_rate}Hz)",
        sample_rate=target_sample_rate,
    )
    run_command(command, logger)
    logger(f"生成完成: {output_path.name}")
    return output_path


def resolve_output_dir(video_path: Path, output_dir: Path | None = None) -> Path:
    return output_dir if output_dir is not None else video_path.parent


def run_pipeline(
    video_path: Path,
    on_vocal_path: Path,
    off_vocal_path: Path,
    output_dir: Path | None,
    logger: Logger,
) -> list[Path]:
    ffmpeg_path = find_tool("ffmpeg.exe")
    ffprobe_path = find_tool("ffprobe.exe")

    logger(f"FFmpeg: {ffmpeg_path}")
    logger(f"FFprobe: {ffprobe_path}")
    logger("正在分析输入文件...")

    video_info = probe_media(ffprobe_path, video_path)
    on_vocal_info = probe_media(ffprobe_path, on_vocal_path)
    off_vocal_info = probe_media(ffprobe_path, off_vocal_path)

    if video_info.video_streams == 0:
        raise ProcessingError("字幕视频里没有检测到视频流。")
    if on_vocal_info.audio_streams == 0:
        raise ProcessingError("原唱无损文件里没有检测到音频流。")
    if off_vocal_info.audio_streams == 0:
        raise ProcessingError("伴奏无损文件里没有检测到音频流。")

    log_media_summary(logger, "字幕视频", video_info)
    log_media_summary(logger, "原唱无损", on_vocal_info)
    log_media_summary(logger, "伴奏无损", off_vocal_info)

    warn_duration_mismatch(logger, video_info, on_vocal_info, "原唱无损")
    warn_duration_mismatch(logger, video_info, off_vocal_info, "伴奏无损")

    output_dir = resolve_output_dir(video_path, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    on_output = output_dir / "on_vocal.mkv"
    off_output = output_dir / "off_vocal.mkv"

    outputs = [
        process_output(ffmpeg_path, logger, video_info, on_vocal_info, on_output, "On Vocal"),
        process_output(ffmpeg_path, logger, video_info, off_vocal_info, off_output, "Off Vocal"),
    ]
    logger(f"输出目录: {output_dir}")
    logger("全部处理完成。")
    return outputs
