import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.quality import measure_sample_peak
from voice_enhancer.domain.profile import ProcessingProfile
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.providers import (
    EnhancementResult,
    PassthroughProvider,
    SpeechEnhancementProvider,
)


@dataclass(frozen=True, slots=True)
class ProcessedMedia:
    output_path: Path
    provider: str
    compute_seconds: float
    provider_cost_usd: float | None = None


class MediaProcessingService:
    def __init__(
        self, ffmpeg: FFmpegProcessor, provider: SpeechEnhancementProvider | None = None
    ) -> None:
        self.ffmpeg = ffmpeg
        self.provider = provider or PassthroughProvider()

    async def process(
        self,
        source: Path,
        *,
        kind: MediaKind,
        profile: ProcessingProfile,
        on_remux: Callable[[], Awaitable[None]] | None = None,
    ) -> ProcessedMedia:
        started = time.monotonic()
        work_dir = source.parent / "work"
        work_dir.mkdir(exist_ok=True)
        working_audio = work_dir / "voice.wav"
        await self.ffmpeg.extract_audio(source, working_audio)

        isolated: EnhancementResult = await self.provider.enhance(
            working_audio, profile=profile, output_path=work_dir / "isolated.audio"
        )
        # A neural suppressor can mistake very quiet speech for silence. Preserve
        # the input if the output loses >35 dB of peak level. This is a conservative
        # signal-loss guard, not a classifier of speech quality.
        if isolated.provider_name in {"deepfilternet", "gtcrn", "elevenlabs-voice-isolator"}:
            input_peak = await measure_sample_peak(self.ffmpeg, working_audio)
            output_peak = await measure_sample_peak(self.ffmpeg, isolated.output_path)
            if input_peak is not None and input_peak > -75 and (
                output_peak is None or input_peak - output_peak > 35
            ):
                isolated = EnhancementResult(
                    working_audio, f"{isolated.provider_name}-fallback-ffmpeg",
                    isolated.compute_seconds, isolated.estimated_cost_usd,
                )
        enhanced_audio = work_dir / "enhanced.m4a"
        await self.ffmpeg.enhance_audio(isolated.output_path, enhanced_audio, profile)
        if kind is MediaKind.VIDEO:
            if on_remux is not None:
                await on_remux()
            output = work_dir / "enhanced.mp4"
            await self.ffmpeg.remux_video(source, enhanced_audio, output)
        else:
            output = enhanced_audio
        return ProcessedMedia(
            output,
            isolated.provider_name,
            time.monotonic() - started,
            isolated.estimated_cost_usd,
        )
