from dataclasses import dataclass
from pathlib import Path

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.domain.profile import ProcessingProfile
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.providers import EnhancementResult


@dataclass(frozen=True, slots=True)
class ProcessedMedia:
    output_path: Path
    provider: str
    compute_seconds: float


class MediaProcessingService:
    def __init__(self, ffmpeg: FFmpegProcessor) -> None:
        self.ffmpeg = ffmpeg

    async def process(
        self, source: Path, *, kind: MediaKind, profile: ProcessingProfile
    ) -> ProcessedMedia:
        work_dir = source.parent / "work"
        work_dir.mkdir(exist_ok=True)
        working_audio = work_dir / "voice.wav"
        if kind is MediaKind.VIDEO:
            await self.ffmpeg.extract_audio(source, working_audio)
        else:
            working_audio = source

        enhanced_audio = work_dir / "enhanced.m4a"
        result: EnhancementResult = await self.ffmpeg.enhance_audio(
            working_audio, enhanced_audio, profile
        )
        if kind is MediaKind.VIDEO:
            output = work_dir / "enhanced.mp4"
            await self.ffmpeg.remux_video(source, enhanced_audio, output)
        else:
            output = enhanced_audio
        return ProcessedMedia(output, result.provider_name, result.compute_seconds)
