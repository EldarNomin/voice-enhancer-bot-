import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from voice_enhancer.application.media_validation import MediaKind
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.application.quality import check_media
from voice_enhancer.benchmark import prepare
from voice_enhancer.config import settings
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.domain.profile_patch import apply_profile_patch
from voice_enhancer.infrastructure.ffmpeg import FFmpegError, FFmpegProcessor
from voice_enhancer.offline import process_file


def _binaries() -> tuple[str, str]:
    ffmpeg = os.getenv("FFMPEG_BIN") or shutil.which("ffmpeg")
    ffprobe = os.getenv("FFPROBE_BIN") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and ffprobe are required for media integration tests")
    return ffmpeg, ffprobe


@pytest.mark.asyncio
async def test_process_timeout_stops_child() -> None:
    processor = FFmpegProcessor(timeout_seconds=0.01)
    with pytest.raises(FFmpegError, match="timed out"):
        await processor._run(sys.executable, "-c", "import time; time.sleep(5)")


@pytest.mark.parametrize("preset", list(Preset))
@pytest.mark.asyncio
async def test_real_audio_and_video_pipeline_preserves_media(
    tmp_path: Path, preset: Preset
) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    processor = FFmpegProcessor(ffmpeg_bin, ffprobe_bin)
    source = tmp_path / "source.mp4"
    await processor._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:r=25:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        str(source),
    )
    result = await MediaProcessingService(processor).process(
        source, kind=MediaKind.VIDEO, profile=profile_for(preset)
    )
    checks = await check_media(processor, source, result.output_path, MediaKind.VIDEO)
    assert checks["video_preserved"]
    assert checks["sample_rate_hz"] == 48000
    assert abs(checks["duration_drift_ms"]) <= 250


@pytest.mark.asyncio
async def test_benchmark_prepares_anonymous_samples_from_real_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    monkeypatch.setattr(settings, "ffmpeg_bin", ffmpeg_bin)
    monkeypatch.setattr(settings, "ffprobe_bin", ffprobe_bin)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    await FFmpegProcessor(ffmpeg_bin, ffprobe_bin)._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        str(corpus / "voice.wav"),
    )
    output = tmp_path / "benchmark"
    deepfilter_bin = os.getenv("DEEPFILTER_BIN")
    providers = ["ffmpeg"] + (["deepfilter"] if deepfilter_bin else [])
    await prepare(
        corpus,
        output,
        provider_names=providers,
        preset=Preset.NATURAL,
        seed=1,
        allow_small=True,
        deepfilter_bin=deepfilter_bin,
    )
    assert sorted(path.name for path in (output / "samples" / "clip001").iterdir()) == [
        f"{chr(ord('A') + index)}.m4a" for index in range(len(providers) + 1)
    ]
    assert (output / "ratings.csv").is_file()
    assert (output / "mapping.private.json").is_file()


@pytest.mark.asyncio
async def test_offline_processes_audio_without_telegram(tmp_path: Path) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    source = tmp_path / "source.wav"
    await FFmpegProcessor(ffmpeg_bin, ffprobe_bin)._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        str(source),
    )
    output = tmp_path / "result.m4a"
    summary = await process_file(
        source,
        output,
        preset=Preset.NATURAL,
        provider_name="ffmpeg",
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
    )
    assert output.is_file()
    assert summary["quality_checks"]["sample_rate_hz"] == 48000
    with pytest.raises(ValueError, match="already exists"):
        await process_file(
            source,
            output,
            preset=Preset.NATURAL,
            provider_name="ffmpeg",
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )


@pytest.mark.asyncio
async def test_offline_applies_validated_text_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    source = tmp_path / "source.wav"
    await FFmpegProcessor(ffmpeg_bin, ffprobe_bin)._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1",
        str(source),
    )
    expected = apply_profile_patch(profile_for(Preset.STUDIO), {"warmth": "+0.10"})
    interpreter = AsyncMock(return_value=expected)

    class FakeInterpreter:
        interpret = interpreter

    monkeypatch.setattr("voice_enhancer.offline.GlmProfileInterpreter", lambda _: FakeInterpreter())
    output = tmp_path / "result.m4a"
    summary = await process_file(
        source,
        output,
        preset=Preset.STUDIO,
        provider_name="ffmpeg",
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        instruction="Сделай голос мягче",
    )
    assert output.is_file()
    assert summary["applied_profile_changes"] == {"warmth": 0.55}
    interpreter.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_deepfilter_binary_works_when_configured(
    tmp_path: Path,
) -> None:
    executable = os.getenv("DEEPFILTER_BIN")
    if not executable or not Path(executable).is_file():
        pytest.skip("Optional DeepFilterNet binary is not configured")
    ffmpeg_bin, ffprobe_bin = _binaries()
    source = tmp_path / "source.wav"
    await FFmpegProcessor(ffmpeg_bin, ffprobe_bin)._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        str(source),
    )
    output = tmp_path / "result.m4a"
    summary = await process_file(
        source,
        output,
        preset=Preset.STUDIO,
        provider_name="deepfilter",
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        deepfilter_bin=executable,
    )
    assert summary["provider"] == "deepfilternet"
    assert output.is_file()


@pytest.mark.parametrize(
    ("suffix", "codec"),
    [
        (".wav", "pcm_s16le"),
        (".mp3", "libmp3lame"),
        (".m4a", "aac"),
        (".aac", "aac"),
        (".ogg", "libopus"),
        (".opus", "libopus"),
        (".flac", "flac"),
    ],
)
@pytest.mark.asyncio
async def test_supported_audio_inputs_decode_and_process(
    tmp_path: Path, suffix: str, codec: str
) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    processor = FFmpegProcessor(ffmpeg_bin, ffprobe_bin)
    source = tmp_path / f"source{suffix}"
    await processor._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:a",
        codec,
        str(source),
    )
    result = await MediaProcessingService(processor).process(
        source, kind=MediaKind.AUDIO, profile=profile_for(Preset.STUDIO)
    )
    checks = await check_media(processor, source, result.output_path, MediaKind.AUDIO)
    assert checks["decodable"]


@pytest.mark.parametrize(
    ("suffix", "video_codec", "audio_codec"),
    [
        (".mp4", "mpeg4", "aac"),
        (".mov", "mpeg4", "aac"),
        (".mkv", "mpeg4", "aac"),
        (".webm", "libvpx-vp9", "libopus"),
    ],
)
@pytest.mark.asyncio
async def test_supported_video_inputs_remux_and_process(
    tmp_path: Path, suffix: str, video_codec: str, audio_codec: str
) -> None:
    ffmpeg_bin, ffprobe_bin = _binaries()
    processor = FFmpegProcessor(ffmpeg_bin, ffprobe_bin)
    source = tmp_path / f"source{suffix}"
    await processor._run(
        ffmpeg_bin,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:r=25:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=2",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        video_codec,
        "-c:a",
        audio_codec,
        str(source),
    )
    result = await MediaProcessingService(processor).process(
        source, kind=MediaKind.VIDEO, profile=profile_for(Preset.STUDIO)
    )
    checks = await check_media(processor, source, result.output_path, MediaKind.VIDEO)
    assert checks["video_preserved"]
