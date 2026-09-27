from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from voice_enhancer.application.media_validation import MediaKind
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
        work_dir = source.parent / "work"
        work_dir.mkdir(exist_ok=True)
        working_audio = work_dir / "voice.wav"
        await self.ffmpeg.extract_audio(source, working_audio)

        isolated: EnhancementResult = await self.provider.enhance(
            working_audio, profile=profile, output_path=work_dir / "isolated.audio"
        )
        enhanced_audio = work_dir / "enhanced.m4a"
        dsp = await self.ffmpeg.enhance_audio(isolated.output_path, enhanced_audio, profile)
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
            isolated.compute_seconds + dsp.compute_seconds,
            isolated.estimated_cost_usd,
        )
