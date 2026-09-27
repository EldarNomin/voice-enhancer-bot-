from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from voice_enhancer.i18n import tr


class MediaKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"


VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".flac"}


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    kind: MediaKind
    duration_seconds: float
    size_bytes: int
    has_audio: bool
    has_video: bool


class MediaValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(tr("ru", code))


def validate_media(
    path: Path,
    metadata: MediaMetadata,
    *,
    max_duration_seconds: int = 600,
    max_size_bytes: int = 2 * 1024**3,
) -> None:
    suffix = path.suffix.lower()
    allowed = VIDEO_EXTENSIONS if metadata.kind is MediaKind.VIDEO else AUDIO_EXTENSIONS
    if suffix not in allowed:
        raise MediaValidationError("unsupported_format")
    if metadata.size_bytes <= 0 or metadata.size_bytes > max_size_bytes:
        raise MediaValidationError("file_too_large")
    if metadata.duration_seconds <= 0 or metadata.duration_seconds > max_duration_seconds:
        raise MediaValidationError("file_too_long")
    if not metadata.has_audio:
        raise MediaValidationError("no_audio")
    if metadata.kind is MediaKind.VIDEO and not metadata.has_video:
        raise MediaValidationError("no_video")
