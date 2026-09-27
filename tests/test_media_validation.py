from pathlib import Path

import pytest

from voice_enhancer.application.media_validation import (
    MediaKind,
    MediaMetadata,
    MediaValidationError,
    validate_media,
)


def test_accepts_valid_video() -> None:
    validate_media(Path("clip.mp4"), MediaMetadata(MediaKind.VIDEO, 18, 1024, True, True))


@pytest.mark.parametrize(
    "path,metadata",
    [
        ("clip.exe", MediaMetadata(MediaKind.VIDEO, 10, 100, True, True)),
        ("clip.mp4", MediaMetadata(MediaKind.VIDEO, 601, 100, True, True)),
        ("clip.mp4", MediaMetadata(MediaKind.VIDEO, 10, 100, False, True)),
    ],
)
def test_rejects_invalid_media(path: str, metadata: MediaMetadata) -> None:
    with pytest.raises(MediaValidationError):
        validate_media(Path(path), metadata)
