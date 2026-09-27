"""Technical checks for media output before delivery or listening review."""

import asyncio
import re
from pathlib import Path

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor


def _duration(info: dict) -> float:
    return float(info.get("format", {}).get("duration") or 0)


def _stream(info: dict, kind: str) -> dict | None:
    return next((item for item in info.get("streams", []) if item.get("codec_type") == kind), None)


async def measure_sample_peak(ffmpeg: FFmpegProcessor, path: Path) -> float | None:
    process = await asyncio.create_subprocess_exec(
        ffmpeg.ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=ffmpeg.timeout_seconds)
    except (TimeoutError, asyncio.CancelledError) as error:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        if isinstance(error, TimeoutError):
            raise TimeoutError("Audio peak measurement timed out") from error
        raise
    if process.returncode:
        raise ValueError("Could not measure output audio peak")
    match = re.search(
        r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", stderr.decode(errors="replace")
    )
    if match is None:
        raise ValueError("FFmpeg did not report output audio peak")
    return None if match.group(1) == "-inf" else float(match.group(1))


async def check_media(ffmpeg: FFmpegProcessor, source: Path, result: Path, kind: MediaKind) -> dict:
    source_info, result_info = await asyncio.gather(ffmpeg.probe(source), ffmpeg.probe(result))
    source_duration = _duration(source_info)
    result_duration = _duration(result_info)
    if source_duration <= 0 or result_duration <= 0:
        raise ValueError("Source or result has no duration")
    audio = _stream(result_info, "audio")
    if audio is None:
        raise ValueError("Result has no audio stream")
    if int(audio.get("sample_rate") or 0) != 48000:
        raise ValueError("Output audio must be 48 kHz")
    if abs(result_duration - source_duration) > 0.25:
        raise ValueError(f"Duration drift exceeds 250 ms: {result_duration - source_duration:.3f}s")
    av_duration_drift_ms = None
    if kind is MediaKind.VIDEO:
        before, after = _stream(source_info, "video"), _stream(result_info, "video")
        if before is None or after is None:
            raise ValueError("Video stream missing")
        for field in ("width", "height", "r_frame_rate", "codec_name"):
            if before.get(field) != after.get(field):
                raise ValueError(f"Video {field} changed")
        video_duration = float(after.get("duration") or result_duration)
        audio_duration = float(audio.get("duration") or result_duration)
        av_duration_drift_ms = round((audio_duration - video_duration) * 1000)
        if abs(av_duration_drift_ms) > 250:
            raise ValueError("Output audio/video duration differs by more than 250 ms")
    await ffmpeg._run(
        ffmpeg.ffmpeg_bin, "-v", "error", "-nostdin", "-i", str(result), "-f", "null", "-"
    )
    peak = await measure_sample_peak(ffmpeg, result)
    if peak is not None and peak >= 0:
        raise ValueError("Output audio reaches digital clipping")
    return {
        "duration_seconds": round(result_duration, 3),
        "duration_drift_ms": round((result_duration - source_duration) * 1000),
        "sample_rate_hz": int(audio.get("sample_rate") or 0),
        "sample_peak_dbfs": peak,
        "av_duration_drift_ms": av_duration_drift_ms,
        "video_preserved": kind is MediaKind.AUDIO or _stream(result_info, "video") is not None,
        "decodable": True,
    }
