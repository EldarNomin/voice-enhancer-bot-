import asyncio
import json
import time
from pathlib import Path

from voice_enhancer.domain.profile import ProcessingProfile
from voice_enhancer.infrastructure.providers import EnhancementResult


class FFmpegError(RuntimeError):
    pass


class FFmpegProcessor:
    """Metadata inspection, conservative speech DSP, and video remux operations."""

    def __init__(
        self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe", timeout_seconds: int = 1800
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.timeout_seconds = timeout_seconds

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except (TimeoutError, asyncio.CancelledError) as error:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(error, TimeoutError):
                raise FFmpegError("FFmpeg command timed out") from error
            raise
        if process.returncode:
            detail = stderr.decode(errors="replace")[-2000:]
            raise FFmpegError(f"Command failed ({process.returncode}): {detail}")
        return stdout.decode(errors="replace")

    async def probe(self, path: Path) -> dict:
        raw = await self._run(
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        )
        return json.loads(raw)

    async def extract_audio(self, source: Path, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(
            self.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        )

    async def enhance_audio(
        self, input_path: Path, output_path: Path, profile: ProcessingProfile
    ) -> EnhancementResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Values are derived from validated bounded profile fields; no user text enters FFmpeg args.
        # afftdn's noise floor accepts -80..-20 dB; all preset values must stay valid.
        nr = 20 + profile.noise_reduction * 20
        threshold = -18 + profile.compression * 8
        presence = profile.presence * 2.5
        warmth = profile.warmth * 1.5
        filters = (
            f"highpass=f=75,afftdn=nf=-{nr:.1f},"
            f"equalizer=f=180:t=q:w=1:g={warmth:.2f},"
            f"equalizer=f=3500:t=q:w=1:g={presence:.2f},"
            f"acompressor=threshold={threshold:.1f}dB:ratio=2.5:attack=15:release=120,"
            f"loudnorm=I={profile.target_lufs:.1f}:TP={profile.true_peak_db:.1f}:LRA=11"
        )
        started = time.monotonic()
        await self._run(
            self.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-af",
            filters,
            "-ar",
            "48000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        )
        return EnhancementResult(output_path, "ffmpeg-dsp-baseline", time.monotonic() - started)

    async def remux_video(self, source: Path, enhanced_audio: Path, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        await self._run(
            self.ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-i",
            str(enhanced_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map_metadata",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        )
