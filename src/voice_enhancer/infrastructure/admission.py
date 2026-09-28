"""Single-host admission controls shared by both messengers on the media volume."""
import hashlib
import io
import shutil
from contextlib import contextmanager
from pathlib import Path

from voice_enhancer.config import settings


class AdmissionRejected(RuntimeError):
    def __init__(self, code: str = "server_busy") -> None:
        self.code = code
        super().__init__(code)


def require_disk_space(root: Path, incoming_bytes: int = 0) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # Conservative reservation for all concurrent downloads plus one worker's
    # decoded PCM, provider output and remux. The deployment uses one volume.
    # Use the same reservation for both channels, even when MAX has a lower cap.
    downloads = max(incoming_bytes, settings.max_media_size_bytes) if incoming_bytes else 0
    pcm = int(settings.max_media_duration_seconds * 48000 * 2 * 4 * 3)
    needed = (settings.min_free_disk_bytes + settings.max_media_size_bytes + pcm
              + downloads * settings.max_concurrent_uploads)
    if shutil.disk_usage(root).free < needed:
        raise AdmissionRejected("storage_busy")


@contextmanager
def upload_slot(root: Path, channel: str, user_id: int, incoming_bytes: int):
    import fcntl  # Containers run Linux, including Docker Desktop on Windows.

    folder = root / ".admission"
    folder.mkdir(parents=True, exist_ok=True)
    # Fixed lock stripes avoid a permanent file per user. Hash collisions only
    # cause a conservative busy response; identities are never combined.
    stripe = int.from_bytes(hashlib.sha256(f"{channel}:{user_id}".encode()).digest()[:2]) % 256
    user_lock = (folder / f"user-{stripe}.lock").open("a+")
    slot = None
    try:
        try:
            fcntl.flock(user_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AdmissionRejected() from None
        for index in range(settings.max_concurrent_uploads):
            candidate = (folder / f"slot-{index}.lock").open("a+")
            try:
                fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                candidate.close()
            else:
                slot = candidate
                break
        if slot is None:
            raise AdmissionRejected()
        require_disk_space(root, incoming_bytes)
        yield
    finally:
        if slot is not None:
            slot.close()
        user_lock.close()  # OS releases both locks on cancellation/crash, too.


class BoundedWriter(io.BufferedWriter):
    """Enforce the real streamed byte count, not just messenger metadata."""
    def __init__(self, path: Path, limit: int) -> None:
        super().__init__(path.open("wb", buffering=0))
        self.limit = limit
        self.written = 0

    def write(self, data):
        if self.written + len(data) > self.limit:
            from voice_enhancer.application.media_validation import MediaValidationError
            raise MediaValidationError("file_too_large")
        count = super().write(data)
        self.written += count
        return count
