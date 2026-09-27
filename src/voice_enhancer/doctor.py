"""Report local prerequisites without printing credentials or configuration values."""

import json
import shutil
import sys
from pathlib import Path

from voice_enhancer.config import Settings


def _executable_exists(name: str) -> bool:
    return Path(name).is_file() or shutil.which(name) is not None


def diagnose(settings: Settings) -> dict:
    ffmpeg = _executable_exists(settings.ffmpeg_bin)
    ffprobe = _executable_exists(settings.ffprobe_bin)
    docker = shutil.which("docker") is not None
    provider_ready = (
        settings.enhancement_provider == "ffmpeg"
        or settings.enhancement_provider == "elevenlabs"
        and bool(settings.elevenlabs_api_key)
        or settings.enhancement_provider == "deepfilter"
        and _executable_exists(settings.deepfilter_bin)
    )
    checks = {
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "ffmpeg_available": ffmpeg,
        "ffprobe_available": ffprobe,
        "docker_available": docker,
        "bot_token_set": bool(settings.bot_token),
        "telegram_api_id_set": bool(settings.telegram_api_id),
        "telegram_api_hash_set": bool(settings.telegram_api_hash),
        "selected_provider_ready": provider_ready,
    }
    return {
        "checks": checks,
        "offline_processing_ready": all(
            (checks["python_3_12_or_newer"], ffmpeg, ffprobe, provider_ready)
        ),
        "docker_bot_ready": all(
            (
                docker,
                checks["bot_token_set"],
                checks["telegram_api_id_set"],
                checks["telegram_api_hash_set"],
                settings.enhancement_provider == "ffmpeg"
                or settings.enhancement_provider == "elevenlabs"
                and bool(settings.elevenlabs_api_key),
            )
        ),
    }


def main() -> None:
    print(json.dumps(diagnose(Settings()), indent=2))


if __name__ == "__main__":
    main()
