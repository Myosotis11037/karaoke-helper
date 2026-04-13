from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from krok_helper.errors import ProcessingError
from krok_helper.models import MediaInfo
from krok_helper.types import Logger


def find_tool(tool_name: str) -> str:
    root = Path(__file__).resolve().parent.parent
    local_candidate = root / "ffmpeg" / "bin" / tool_name
    if local_candidate.exists():
        return str(local_candidate)

    resolved = shutil.which(tool_name)
    if resolved:
        return resolved

    raise ProcessingError(
        f"找不到 {tool_name}。请把 ffmpeg/ffprobe 放到程序目录下的 ffmpeg/bin，或加入系统 PATH。"
    )


def probe_media(ffprobe_path: str, media_path: Path) -> MediaInfo:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(media_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ProcessingError(f"无法读取媒体信息: {media_path.name}\n{result.stderr.strip()}")

    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    format_info = payload.get("format", {})

    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]

    duration_raw = format_info.get("duration")
    duration = float(duration_raw) if duration_raw not in (None, "N/A", "") else 0.0

    sample_rate = None
    channels = None
    if audio_streams:
        first_audio = audio_streams[0]
        sample_rate_raw = first_audio.get("sample_rate")
        channels_raw = first_audio.get("channels")
        sample_rate = int(sample_rate_raw) if sample_rate_raw not in (None, "N/A", "") else None
        channels = int(channels_raw) if channels_raw not in (None, "N/A", "") else None

    return MediaInfo(
        path=media_path,
        duration=duration,
        video_streams=len(video_streams),
        audio_streams=len(audio_streams),
        subtitle_streams=len(subtitle_streams),
        sample_rate=sample_rate,
        channels=channels,
    )


def run_command(command: list[str], logger: Logger) -> None:
    logger("执行命令:")
    logger(" ".join(f'"{part}"' if " " in part else part for part in command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.strip()
        if line:
            logger(line)

    return_code = process.wait()
    if return_code != 0:
        raise ProcessingError(f"ffmpeg 执行失败，退出码: {return_code}")
