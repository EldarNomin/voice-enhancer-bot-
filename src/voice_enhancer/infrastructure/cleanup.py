import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voice_enhancer.domain.job import JobStatus
from voice_enhancer.infrastructure.database import JobStore


def _safe_job_folder(root: Path, job_id: str) -> Path:
    if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
        raise ValueError("Invalid job ID in cleanup")
    folder = (root / job_id).resolve()
    if folder.parent != root.resolve():
        raise ValueError("Job folder escapes media root")
    return folder


async def cleanup_media(store: JobStore, root: Path, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    root = root.resolve()
    for job_id in await store.expire_pending(now - timedelta(minutes=30)):
        shutil.rmtree(_safe_job_folder(root, job_id), ignore_errors=True)

    for job in await store.finished_before(now - timedelta(hours=24)):
        folder = _safe_job_folder(root, job.id)
        updated_at = (
            job.updated_at.replace(tzinfo=UTC) if job.updated_at.tzinfo is None else job.updated_at
        )
        if job.status != JobStatus.COMPLETED.value or updated_at < now - timedelta(hours=72):
            shutil.rmtree(folder, ignore_errors=True)
        else:
            source = Path(job.source_path).resolve()
            if source.parent == folder:
                source.unlink(missing_ok=True)
