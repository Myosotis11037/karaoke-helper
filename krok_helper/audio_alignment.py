from __future__ import annotations

import array
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from krok_helper.errors import ProcessingError
from krok_helper.ffmpeg import _build_subprocess_kwargs, find_tool, probe_media, run_command
from krok_helper.types import Logger


WAVEFORM_SAMPLE_RATE = 8_000
WAVEFORM_PEAKS_PER_SECOND = 80
ALIGNED_VIDEO_EXTENSION = ".mkv"
ENCODE_MODE_SOFTWARE = "software"
ENCODE_MODE_HARDWARE = "hardware"
COMMON_VIDEO_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "mpeg4": "mpeg4",
    "vp8": "libvpx",
    "vp9": "libvpx-vp9",
}
NVENC_VIDEO_ENCODERS = {
    "h264": "h264_nvenc",
    "hevc": "hevc_nvenc",
}
COMMON_AUDIO_ENCODERS = {
    "aac": "aac",
    "flac": "flac",
    "mp3": "libmp3lame",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
    "pcm_s32le": "pcm_s32le",
}


@dataclass
class WaveformData:
    path: Path
    duration: float
    peaks_per_second: int
    peaks: list[float]


@dataclass
class AlignmentPreviewProcess:
    ffmpeg_process: subprocess.Popen
    ffplay_process: subprocess.Popen

    def is_running(self) -> bool:
        return self.ffplay_process.poll() is None

    def stop(self) -> None:
        for process in (self.ffplay_process, self.ffmpeg_process):
            if process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


def format_offset(seconds: float) -> str:
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{abs(seconds):.3f}s"


def default_aligned_video_path(video_path: Path) -> Path:
    return video_path.with_name(f"{video_path.stem}_aligned{ALIGNED_VIDEO_EXTENSION}")


def default_aligned_audio_path(audio_path: Path) -> Path:
    return audio_path.with_name(f"{audio_path.stem}_aligned.wav")


def _build_waveform_command(ffmpeg_path: str, media_path: Path) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        "-i",
        str(media_path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(WAVEFORM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def _format_seconds_for_ffmpeg(seconds: float) -> str:
    text = f"{max(0.0, seconds):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _preview_input_args(media_path: Path, timeline_offset: float, preview_start: float) -> tuple[list[str], float]:
    source_start = max(0.0, preview_start - timeline_offset)
    preview_delay = max(0.0, timeline_offset - preview_start)
    args: list[str] = []
    if source_start > 0.001:
        args.extend(["-ss", _format_seconds_for_ffmpeg(source_start)])
    args.extend(["-i", str(media_path)])
    return args, preview_delay


def _preview_filter(input_index: int, preview_delay: float, label: str) -> str:
    filters = [f"[{input_index}:a:0]asetpts=PTS-STARTPTS"]
    if preview_delay > 0.001:
        delay_ms = max(0, int(round(preview_delay * 1000)))
        filters.append(f"adelay={delay_ms}:all=1")
    return ",".join(filters) + f"[{label}]"


def _probe_payload(ffprobe_path: str, media_path: Path) -> dict:
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
        **_build_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise ProcessingError(f"无法读取媒体参数: {media_path.name}\n{result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def _first_video_stream(payload: dict) -> dict:
    for stream in payload.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    raise ProcessingError("源视频里没有检测到视频流。")


def _audio_streams(payload: dict) -> list[dict]:
    return [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]


def _subtitle_streams(payload: dict) -> list[dict]:
    return [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "subtitle"]


def _stream_count(payload: dict, codec_type: str) -> int:
    return sum(1 for stream in payload.get("streams", []) if stream.get("codec_type") == codec_type)


def _duration_from_payload(payload: dict) -> float | None:
    format_info = payload.get("format", {})
    raw_duration = format_info.get("duration")
    if raw_duration not in (None, "", "N/A"):
        try:
            duration = float(raw_duration)
            if duration > 0:
                return duration
        except (TypeError, ValueError):
            pass

    durations: list[float] = []
    for stream in payload.get("streams", []):
        raw_stream_duration = stream.get("duration")
        if raw_stream_duration in (None, "", "N/A"):
            continue
        try:
            duration = float(raw_stream_duration)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            durations.append(duration)

    if durations:
        return max(durations)
    return None


def _parse_fraction(raw_value: str | None, fallback: str = "30") -> str:
    if not raw_value or raw_value == "0/0":
        return fallback
    return raw_value


def _channel_layout(audio_stream: dict) -> str:
    layout = str(audio_stream.get("channel_layout") or "").strip()
    if layout and layout != "unknown":
        return layout

    channels_raw = audio_stream.get("channels")
    try:
        channels = int(channels_raw)
    except (TypeError, ValueError):
        channels = 2
    if channels == 1:
        return "mono"
    if channels == 2:
        return "stereo"
    return f"{channels}c"


def _video_encoding_options(video_stream: dict, encode_mode: str = ENCODE_MODE_SOFTWARE) -> list[str]:
    video_codec = str(video_stream.get("codec_name") or "h264")
    if encode_mode == ENCODE_MODE_HARDWARE and video_codec in NVENC_VIDEO_ENCODERS:
        return [
            "-c:v",
            NVENC_VIDEO_ENCODERS[video_codec],
            "-preset",
            "p1",
            "-rc",
            "constqp",
            "-qp",
            "18",
        ]

    video_encoder = COMMON_VIDEO_ENCODERS.get(video_codec, "libx264")
    options = ["-c:v", video_encoder]

    if video_encoder == "libx264":
        profile = str(video_stream.get("profile") or "").strip().lower()
        if profile and "baseline" not in profile:
            options.extend(["-profile:v", profile.replace(" ", "")])
        options.extend(["-preset", "veryfast", "-crf", "18"])
    elif video_encoder == "libx265":
        options.extend(["-preset", "veryfast", "-crf", "23"])

    return options


def _audio_encoding_options(audio_stream: dict, stream_index: int | None = None) -> list[str]:
    audio_codec = str(audio_stream.get("codec_name") or "aac")
    audio_encoder = COMMON_AUDIO_ENCODERS.get(audio_codec, "aac")
    suffix = "" if stream_index is None else f":a:{stream_index}"
    options = [f"-c:a{suffix}", audio_encoder]

    sample_rate = audio_stream.get("sample_rate")
    if sample_rate:
        options.extend([f"-ar:a{suffix}", str(sample_rate)])
    channels = audio_stream.get("channels")
    if channels:
        options.extend([f"-ac:a{suffix}", str(channels)])
    bit_rate = audio_stream.get("bit_rate")
    if bit_rate and audio_encoder in {"aac", "libmp3lame", "libopus", "libvorbis"}:
        options.extend([f"-b:a{suffix}", str(bit_rate)])

    return options


def _concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def _samples_from_pcm(raw: bytes) -> array.array:
    samples = array.array("h")
    if raw:
        samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _build_peaks(samples: array.array, window_size: int) -> list[float]:
    peaks: list[float] = []
    max_sample = 32768.0
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        peak = max((abs(value) for value in window), default=0)
        peaks.append(min(1.0, peak / max_sample))
    return peaks


def extract_waveform(
    media_path: Path,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    label: str,
) -> WaveformData:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    info = probe_media(ffprobe_path, media_path)
    if info.audio_streams == 0:
        raise ProcessingError(f"{label} 里没有检测到音频流。")

    logger(f"正在生成 {label} 波形: {media_path.name}")
    result = subprocess.run(
        _build_waveform_command(ffmpeg_path, media_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **_build_subprocess_kwargs(),
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(f"{label} 波形生成失败: {media_path.name}\n{stderr}")

    samples = _samples_from_pcm(result.stdout)
    if not samples:
        raise ProcessingError(f"{label} 没有可用于绘制波形的音频采样。")

    window_size = max(1, WAVEFORM_SAMPLE_RATE // WAVEFORM_PEAKS_PER_SECOND)
    peaks = _build_peaks(samples, window_size)
    duration = info.duration or (len(samples) / WAVEFORM_SAMPLE_RATE)
    logger(f"{label} 波形完成: {len(peaks)} 个峰值点，时长 {duration:.3f}s")
    return WaveformData(
        path=media_path,
        duration=duration,
        peaks_per_second=WAVEFORM_PEAKS_PER_SECOND,
        peaks=peaks,
    )


def _build_black_segment_command(
    ffmpeg_path: str,
    source_payload: dict,
    output_path: Path,
    duration_seconds: float,
) -> list[str]:
    video_stream = _first_video_stream(source_payload)
    audio_streams = _audio_streams(source_payload)

    width = int(video_stream.get("width") or 1920)
    height = int(video_stream.get("height") or 1080)
    frame_rate = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    pixel_format = str(video_stream.get("pix_fmt") or "yuv420p")

    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={width}x{height}:r={frame_rate}:d={duration_seconds:.6f}",
    ]

    for audio_stream in audio_streams:
        sample_rate = str(audio_stream.get("sample_rate") or "48000")
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{duration_seconds:.6f}",
                "-i",
                f"anullsrc=channel_layout={_channel_layout(audio_stream)}:sample_rate={sample_rate}",
            ]
        )

    command.extend(["-map", "0:v:0"])
    for index in range(len(audio_streams)):
        command.extend(["-map", f"{index + 1}:a:0"])

    command.extend(_video_encoding_options(video_stream))
    command.extend(["-pix_fmt", pixel_format, "-r", frame_rate])

    for index, audio_stream in enumerate(audio_streams):
        command.extend(_audio_encoding_options(audio_stream, index))

    command.extend(["-map_metadata", "-1", str(output_path)])
    return command


def _build_concat_copy_command(ffmpeg_path: str, concat_list_path: Path, output_path: Path) -> list[str]:
    return [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-map",
        "0",
        "-c",
        "copy",
        str(output_path),
    ]


def _build_timestamp_normalization_command(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
) -> list[str]:
    return [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-fflags",
        "+genpts",
        "-i",
        str(input_path),
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(output_path),
    ]


def _validate_concat_output(
    ffprobe_path: str,
    source_payload: dict,
    output_path: Path,
    offset_seconds: float,
    logger: Logger,
) -> None:
    output_payload = _probe_payload(ffprobe_path, output_path)
    for codec_type, label in [("video", "视频"), ("audio", "音频"), ("subtitle", "字幕")]:
        source_count = _stream_count(source_payload, codec_type)
        output_count = _stream_count(output_payload, codec_type)
        if output_count < source_count:
            raise ProcessingError(
                f"concat 输出缺少{label}流: 源文件 {source_count} 条，输出 {output_count} 条"
            )

    source_duration = _duration_from_payload(source_payload)
    output_duration = _duration_from_payload(output_payload)
    if source_duration is not None and output_duration is not None:
        expected_duration = source_duration + offset_seconds
        tolerance = max(2.0, expected_duration * 0.03)
        lower_limit = max(0.0, expected_duration - tolerance)
        upper_limit = expected_duration + tolerance
        logger(
            "一级策略: 时长校验 "
            f"源 {source_duration:.3f}s + 黑场 {offset_seconds:.3f}s = 预期 {expected_duration:.3f}s，"
            f"输出 {output_duration:.3f}s"
        )
        if not lower_limit <= output_duration <= upper_limit:
            raise ProcessingError(
                "concat 输出时长异常: "
                f"预期约 {expected_duration:.3f}s，实际 {output_duration:.3f}s"
            )
    else:
        logger("一级策略: ffprobe 未能读取完整时长，跳过时长校验")


def _try_export_with_concat_copy(
    ffmpeg_path: str,
    ffprobe_path: str,
    video_path: Path,
    output_path: Path,
    offset_seconds: float,
    logger: Logger,
) -> bool:
    payload = _probe_payload(ffprobe_path, video_path)
    subtitles = _subtitle_streams(payload)
    if subtitles:
        logger(
            f"检测到 {len(subtitles)} 条字幕流，concat 无损拼接可能因流结构不一致而失败，"
            "会先尝试，失败后自动回退。"
        )

    with TemporaryDirectory(prefix="krok-align-concat-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        black_path = temp_dir / "black.mkv"
        concat_list_path = temp_dir / "concat.txt"
        normalized_source_path = temp_dir / "source.normalized.mkv"

        logger("一级策略: 读取源视频参数并生成黑场片段")
        run_command(
            _build_black_segment_command(
                ffmpeg_path=ffmpeg_path,
                source_payload=payload,
                output_path=black_path,
                duration_seconds=offset_seconds,
            ),
            logger,
        )
        concat_list_path.write_text(
            "\n".join([_concat_file_line(black_path), _concat_file_line(video_path)]) + "\n",
            encoding="utf-8",
        )

        logger("一级策略: 尝试 concat -c copy 无损拼接")
        run_command(_build_concat_copy_command(ffmpeg_path, concat_list_path, output_path), logger)
        try:
            _validate_concat_output(ffprobe_path, payload, output_path, offset_seconds, logger)
        except ProcessingError as exc:
            logger(f"一级策略: 直接 concat 校验失败，尝试先无损规范化源视频时间戳: {exc}")
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass

            run_command(
                _build_timestamp_normalization_command(
                    ffmpeg_path,
                    video_path,
                    normalized_source_path,
                ),
                logger,
            )
            concat_list_path.write_text(
                "\n".join([_concat_file_line(black_path), _concat_file_line(normalized_source_path)]) + "\n",
                encoding="utf-8",
            )
            logger("一级策略: 使用规范化源视频再次 concat -c copy")
            run_command(_build_concat_copy_command(ffmpeg_path, concat_list_path, output_path), logger)
            _validate_concat_output(ffprobe_path, payload, output_path, offset_seconds, logger)

    return output_path.is_file() and os.path.getsize(output_path) > 0


def build_aligned_audio_command(
    ffmpeg_path: str,
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    *,
    source_payload: dict | None = None,
) -> list[str]:
    if output_path.suffix.lower() != ".wav":
        output_path = output_path.with_suffix(".wav")

    command = [ffmpeg_path, "-y", "-hide_banner"]
    if offset_seconds < 0:
        command.extend(["-ss", f"{abs(offset_seconds):.6f}"])

    command.extend(
        [
            "-i",
            str(audio_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
        ]
    )

    if offset_seconds > 0:
        delay_ms = max(0, int(round(offset_seconds * 1000)))
        command.extend(["-af", f"adelay={delay_ms}:all=1"])

    audio_streams = _audio_streams(source_payload) if source_payload is not None else []
    first_audio_stream = audio_streams[0] if audio_streams else {}
    source_codec = str(first_audio_stream.get("codec_name") or "").lower()
    sample_format = str(first_audio_stream.get("sample_fmt") or "").lower()
    sample_bits = str(
        first_audio_stream.get("bits_per_raw_sample")
        or first_audio_stream.get("bits_per_sample")
        or ""
    ).lower()
    pcm_codec = "pcm_s16le"
    if "dbl" in sample_format or "pcm_f64" in source_codec or sample_bits == "64":
        pcm_codec = "pcm_f64le"
    elif "flt" in sample_format or "pcm_f32" in source_codec:
        pcm_codec = "pcm_f32le"
    elif sample_bits == "24" or "s24" in sample_format:
        pcm_codec = "pcm_s24le"
    elif sample_bits == "32" or "s32" in sample_format:
        pcm_codec = "pcm_s32le"
    elif sample_bits == "8" or "u8" in sample_format:
        pcm_codec = "pcm_u8"
    command.extend(["-c:a", pcm_codec])

    sample_rate = first_audio_stream.get("sample_rate")
    if sample_rate:
        command.extend(["-ar", str(sample_rate)])
    channels = first_audio_stream.get("channels")
    if channels:
        command.extend(["-ac", str(channels)])

    command.extend(["-f", "wav"])
    command.append(str(output_path))
    return command


def export_aligned_audio(
    audio_path: Path,
    output_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
) -> Path:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    media_info = probe_media(ffprobe_path, audio_path)
    source_payload = _probe_payload(ffprobe_path, audio_path)
    if media_info.audio_streams == 0:
        raise ProcessingError(f"原唱音源里没有检测到音频流: {audio_path.name}")

    if output_path.suffix.lower() != ".wav":
        output_path = output_path.with_suffix(".wav")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger(f"导出对齐音频: {output_path.name}")
    logger(f"原唱音源偏移: {format_offset(offset_seconds)}")
    if offset_seconds < 0:
        logger(f"处理方式: 裁掉原唱音源开头 {abs(offset_seconds):.3f}s")
    elif offset_seconds > 0:
        logger(f"处理方式: 给原唱音源前面补 {offset_seconds:.3f}s 静音")
    else:
        logger("处理方式: 不改变时间轴，仅按目标格式重新封装")
    logger("音频格式: 导出 WAV PCM，保留未压缩音频形态")

    try:
        run_command(
            build_aligned_audio_command(
                ffmpeg_path=ffmpeg_path,
                audio_path=audio_path,
                output_path=output_path,
                offset_seconds=offset_seconds,
                source_payload=source_payload,
            ),
            logger,
        )
    except ProcessingError as exc:
        raise ProcessingError(f"导出对齐音频失败: {output_path.name}\n{exc}") from exc

    if not output_path.is_file() or os.path.getsize(output_path) == 0:
        raise ProcessingError(f"导出失败，未生成有效文件: {output_path}")

    logger(f"对齐音频导出完成: {output_path}")
    return output_path


def build_alignment_preview_command(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    offset_seconds: float,
    *,
    target_track: str,
    preview_start_seconds: float = 0.0,
) -> list[str]:
    preview_start_seconds = max(0.0, preview_start_seconds)
    video_offset = offset_seconds if target_track == "video" else 0.0
    audio_offset = offset_seconds if target_track == "audio" else 0.0
    video_args, video_delay = _preview_input_args(video_path, video_offset, preview_start_seconds)
    audio_args, audio_delay = _preview_input_args(audio_path, audio_offset, preview_start_seconds)
    filter_graph = ";".join(
        [
            _preview_filter(0, video_delay, "video_preview"),
            _preview_filter(1, audio_delay, "audio_preview"),
            "[video_preview][audio_preview]amix=inputs=2:duration=longest:dropout_transition=0,volume=0.5[out]",
        ]
    )

    return [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "error",
        *video_args,
        *audio_args,
        "-filter_complex",
        filter_graph,
        "-map",
        "[out]",
        "-vn",
        "-sn",
        "-dn",
        "-f",
        "wav",
        "pipe:1",
    ]


def start_alignment_preview(
    video_path: Path,
    audio_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    *,
    target_track: str,
    preview_start_seconds: float = 0.0,
) -> AlignmentPreviewProcess:
    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffplay_path = find_tool("ffplay.exe", ffmpeg_dir)
    moving_label = "字幕视频音轨" if target_track == "video" else "原唱音源"
    logger(
        f"播放预览: 从 {preview_start_seconds:.3f}s 开始，"
        f"{moving_label}偏移 {format_offset(offset_seconds)}"
    )
    logger("预览混音: 字幕视频音轨 + 原唱音源")

    ffmpeg_command = build_alignment_preview_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_path,
        audio_path=audio_path,
        offset_seconds=offset_seconds,
        target_track=target_track,
        preview_start_seconds=preview_start_seconds,
    )
    ffplay_command = [
        ffplay_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nodisp",
        "-autoexit",
        "-i",
        "pipe:0",
    ]

    ffmpeg_process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        **_build_subprocess_kwargs(),
    )
    assert ffmpeg_process.stdout is not None
    try:
        ffplay_process = subprocess.Popen(
            ffplay_command,
            stdin=ffmpeg_process.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_build_subprocess_kwargs(),
        )
    except Exception:
        ffmpeg_process.terminate()
        raise
    finally:
        ffmpeg_process.stdout.close()

    return AlignmentPreviewProcess(
        ffmpeg_process=ffmpeg_process,
        ffplay_process=ffplay_process,
    )


def build_aligned_video_command(
    ffmpeg_path: str,
    video_path: Path,
    output_path: Path,
    offset_seconds: float,
    *,
    has_audio: bool = True,
    source_payload: dict | None = None,
    encode_mode: str = ENCODE_MODE_SOFTWARE,
) -> list[str]:
    command = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
    ]
    if offset_seconds < 0:
        command.extend(["-ss", f"{abs(offset_seconds):.6f}"])

    if offset_seconds <= 0:
        command.extend(
            [
                "-i",
                str(video_path),
                "-map",
                "0",
                "-c",
                "copy",
            ]
        )
        if offset_seconds < 0:
            command.extend(["-avoid_negative_ts", "make_zero"])
        command.append(str(output_path))
        return command

    video_stream = _first_video_stream(source_payload) if source_payload is not None else {}
    audio_streams = _audio_streams(source_payload) if source_payload is not None else []
    first_audio_stream = audio_streams[0] if audio_streams else {}
    frame_rate = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    pixel_format = str(video_stream.get("pix_fmt") or "yuv420p")
    delay_ms = max(0, int(round(offset_seconds * 1000)))
    filters = [
        f"[0:v:0]tpad=start_duration={offset_seconds:.6f}:start_mode=add:color=black,"
        f"setpts=PTS-STARTPTS,fps=fps={frame_rate}[v]"
    ]
    maps = ["-map", "[v]"]
    if has_audio:
        filters.append(f"[0:a:0]adelay={delay_ms}:all=1[a]")
        maps.extend(["-map", "[a]"])

    command.extend(
        [
            "-i",
            str(video_path),
            "-itsoffset",
            f"{offset_seconds:.6f}",
            "-i",
            str(video_path),
            "-filter_complex",
            ";".join(filters),
            *maps,
            "-map",
            "1:s?",
            "-map",
            "1:d?",
            "-map",
            "1:t?",
            "-map_metadata",
            "0",
        ]
    )
    command.extend(_video_encoding_options(video_stream, encode_mode))
    command.extend(["-pix_fmt", pixel_format, "-r", frame_rate])
    if has_audio:
        command.extend(_audio_encoding_options(first_audio_stream))
    command.extend(["-c:s", "copy", "-c:d", "copy", "-c:t", "copy", str(output_path)])
    return command


def export_aligned_video(
    video_path: Path,
    output_path: Path,
    offset_seconds: float,
    ffmpeg_dir: Path | None,
    logger: Logger,
    encode_mode: str = ENCODE_MODE_SOFTWARE,
) -> Path:
    if encode_mode not in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}:
        encode_mode = ENCODE_MODE_SOFTWARE

    ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
    ffprobe_path = find_tool("ffprobe.exe", ffmpeg_dir)
    media_info = probe_media(ffprobe_path, video_path)
    source_payload = _probe_payload(ffprobe_path, video_path)
    if media_info.video_streams == 0:
        raise ProcessingError(f"字幕视频里没有检测到视频流: {video_path.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger(f"导出对齐视频: {output_path.name}")
    logger(f"字幕视频偏移: {format_offset(offset_seconds)}")
    if offset_seconds < 0:
        logger(f"处理方式: 裁掉字幕视频开头 {abs(offset_seconds):.3f}s")
    elif offset_seconds > 0:
        logger(
            f"处理方式: 先尝试生成 {offset_seconds:.3f}s 黑场并 concat -c copy，"
            "失败则自动回退到重编码补黑"
        )
    else:
        logger("处理方式: 不改变时间轴，仅复制视频容器")

    if offset_seconds > 0:
        try:
            if _try_export_with_concat_copy(
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
                video_path=video_path,
                output_path=output_path,
                offset_seconds=offset_seconds,
                logger=logger,
            ):
                logger(f"一级策略成功，对齐视频导出完成: {output_path}")
                return output_path
        except ProcessingError as exc:
            logger(f"一级策略失败，改用二级策略: {exc}")
            try:
                if output_path.exists():
                    output_path.unlink()
            except OSError:
                pass
        video_stream = _first_video_stream(source_payload)
        video_codec = str(video_stream.get("codec_name") or "h264")
        frame_rate = _parse_fraction(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        audio_streams = _audio_streams(source_payload)
        audio_codec = str(audio_streams[0].get("codec_name") or "aac") if audio_streams else "none"
        encode_label = "硬编快速" if encode_mode == ENCODE_MODE_HARDWARE else "软编省空间"
        logger(
            "二级策略: 使用 tpad / adelay 重编码生成前黑视频，"
            f"编码模式={encode_label}，尽量沿用源参数: "
            f"video={video_codec}, fps={frame_rate}, audio={audio_codec}"
        )

    command = build_aligned_video_command(
        ffmpeg_path=ffmpeg_path,
        video_path=video_path,
        output_path=output_path,
        offset_seconds=offset_seconds,
        has_audio=media_info.audio_streams > 0,
        source_payload=source_payload,
        encode_mode=encode_mode,
    )
    try:
        run_command(command, logger)
    except ProcessingError as exc:
        if offset_seconds > 0 and encode_mode == ENCODE_MODE_HARDWARE:
            logger(f"硬编快速失败，自动改用软编省空间重试: {exc}")
            fallback_command = build_aligned_video_command(
                ffmpeg_path=ffmpeg_path,
                video_path=video_path,
                output_path=output_path,
                offset_seconds=offset_seconds,
                has_audio=media_info.audio_streams > 0,
                source_payload=source_payload,
                encode_mode=ENCODE_MODE_SOFTWARE,
            )
            try:
                run_command(fallback_command, logger)
            except ProcessingError as fallback_exc:
                raise ProcessingError(
                    f"导出对齐视频失败: {output_path.name}\n{fallback_exc}"
                ) from fallback_exc
        else:
            raise ProcessingError(f"导出对齐视频失败: {output_path.name}\n{exc}") from exc

    if not output_path.is_file() or os.path.getsize(output_path) == 0:
        raise ProcessingError(f"导出失败，未生成有效文件: {output_path}")

    logger(f"对齐视频导出完成: {output_path}")
    return output_path
