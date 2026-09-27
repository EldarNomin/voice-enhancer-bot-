from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


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
    pass


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
        raise MediaValidationError("Формат файла пока не поддерживается.")
    if metadata.size_bytes <= 0 or metadata.size_bytes > max_size_bytes:
        raise MediaValidationError("Размер файла превышает допустимый лимит.")
    if metadata.duration_seconds <= 0 or metadata.duration_seconds > max_duration_seconds:
        raise MediaValidationError("Длительность файла должна быть не более 10 минут.")
    if not metadata.has_audio:
        raise MediaValidationError("В файле не найдена аудиодорожка.")
    if metadata.kind is MediaKind.VIDEO and not metadata.has_video:
        raise MediaValidationError("В файле не найдена видеодорожка.")
