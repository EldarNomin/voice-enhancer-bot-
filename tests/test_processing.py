from pathlib import Path

import pytest

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.providers import EnhancementResult


class FakeFFmpeg:
    def __init__(self) -> None:
        self.extracted = False
        self.remuxed = False

    async def extract_audio(self, source: Path, output_path: Path) -> None:
        self.extracted = True

    async def enhance_audio(self, input_path: Path, output_path: Path, profile):
        return EnhancementResult(output_path, "test-provider", 1.25)

    async def remux_video(self, source: Path, enhanced_audio: Path, output: Path) -> None:
        self.remuxed = True


@pytest.mark.asyncio
async def test_video_processing_extracts_enhances_and_remuxes(tmp_path: Path) -> None:
    ffmpeg = FakeFFmpeg()
    service = MediaProcessingService(ffmpeg)  # type: ignore[arg-type]
    result = await service.process(
        tmp_path / "clip.mp4", kind=MediaKind.VIDEO, profile=profile_for(Preset.REELS)
    )
    assert ffmpeg.extracted and ffmpeg.remuxed
    assert result.output_path.name == "enhanced.mp4"
    assert result.provider == "ffmpeg-dsp-baseline"
    assert result.compute_seconds == 1.25


@pytest.mark.asyncio
async def test_audio_processing_normalizes_and_does_not_remux(tmp_path: Path) -> None:
    ffmpeg = FakeFFmpeg()
    service = MediaProcessingService(ffmpeg)  # type: ignore[arg-type]
    result = await service.process(
        tmp_path / "voice.ogg", kind=MediaKind.AUDIO, profile=profile_for(Preset.NATURAL)
    )
    assert ffmpeg.extracted and not ffmpeg.remuxed
    assert result.output_path.name == "enhanced.m4a"
