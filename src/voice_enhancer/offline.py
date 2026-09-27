"""Process one local file without starting Telegram or the worker."""

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from voice_enhancer.application.media_validation import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    MediaKind,
    MediaMetadata,
    validate_media,
)
from voice_enhancer.application.processing import MediaProcessingService
from voice_enhancer.application.quality import check_media
from voice_enhancer.config import settings
from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.ffmpeg import FFmpegProcessor
from voice_enhancer.infrastructure.glm import GlmProfileInterpreter
from voice_enhancer.infrastructure.providers import select_provider


async def process_file(
    source: Path,
    output: Path,
    *,
    preset: Preset,
    provider_name: str,
    ffmpeg_bin: str | None = None,
    ffprobe_bin: str | None = None,
    deepfilter_bin: str | None = None,
    instruction: str | None = None,
) -> dict:
    source = source.resolve(strict=True)
    output = output.resolve()
    if source == output:
        raise ValueError("Output path must differ from the source path")
    if output.exists():
        raise ValueError("Output path already exists")
    if source.suffix.lower() in VIDEO_EXTENSIONS:
        kind = MediaKind.VIDEO
        expected_suffix = ".mp4"
    elif source.suffix.lower() in AUDIO_EXTENSIONS:
        kind = MediaKind.AUDIO
        expected_suffix = ".m4a"
    else:
        raise ValueError("Unsupported input format")
    if output.suffix.lower() != expected_suffix:
        raise ValueError(f"Output must use {expected_suffix} extension")
    ffmpeg = FFmpegProcessor(
        ffmpeg_bin or settings.ffmpeg_bin,
        ffprobe_bin or settings.ffprobe_bin,
        settings.ffmpeg_timeout_seconds,
    )
    info = await ffmpeg.probe(source)
    metadata = MediaMetadata(
        kind=kind,
        duration_seconds=float(info.get("format", {}).get("duration") or 0),
        size_bytes=source.stat().st_size,
        has_audio=any(s.get("codec_type") == "audio" for s in info.get("streams", [])),
        has_video=any(s.get("codec_type") == "video" for s in info.get("streams", [])),
    )
    validate_media(
        source,
        metadata,
        max_duration_seconds=settings.max_media_duration_seconds,
        max_size_bytes=settings.max_media_size_bytes,
    )
    provider = select_provider(
        provider_name,
        elevenlabs_api_key=settings.elevenlabs_api_key,
        deepfilter_bin=deepfilter_bin or settings.deepfilter_bin,
    )
    profile = profile_for(preset)
    profile_changes: dict[str, float] = {}
    if instruction is not None:
        profile, profile_changes = await GlmProfileInterpreter(settings.glm_api_key).interpret(
            instruction, profile
        )
    with tempfile.TemporaryDirectory(prefix="voice-enhancer-") as temporary:
        work_source = Path(temporary) / source.name
        shutil.copyfile(source, work_source)
        processed = await MediaProcessingService(ffmpeg, provider).process(
            work_source, kind=kind, profile=profile
        )
        checks = await check_media(ffmpeg, work_source, processed.output_path, kind)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(processed.output_path, output)
    return {
        "source": str(source),
        "output": str(output),
        "preset": preset.value,
        "provider": processed.provider,
        "applied_profile_changes": profile_changes,
        "compute_seconds": round(processed.compute_seconds, 3),
        "quality_checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preset", type=Preset, choices=list(Preset), default=Preset.STUDIO)
    parser.add_argument(
        "--provider", choices=("ffmpeg", "deepfilter", "elevenlabs"), default="ffmpeg"
    )
    parser.add_argument("--ffmpeg-bin")
    parser.add_argument("--ffprobe-bin")
    parser.add_argument("--deepfilter-bin")
    parser.add_argument("--instruction", help="Optional GLM text adjustment; requires GLM_API_KEY")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                process_file(
                    args.source,
                    args.output,
                    preset=args.preset,
                    provider_name=args.provider,
                    ffmpeg_bin=args.ffmpeg_bin,
                    ffprobe_bin=args.ffprobe_bin,
                    deepfilter_bin=args.deepfilter_bin,
                    instruction=args.instruction,
                )
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
