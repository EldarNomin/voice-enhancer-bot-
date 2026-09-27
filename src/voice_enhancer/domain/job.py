from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class JobStatus(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    QUEUED = "queued"
    PROCESSING = "processing"
    REMUXING = "remuxing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    CANCELLED = "cancelled"


TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.CREATED: {JobStatus.VALIDATING, JobStatus.CANCELLED},
    JobStatus.VALIDATING: {JobStatus.QUEUED, JobStatus.FAILED_FINAL, JobStatus.CANCELLED},
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.CANCELLED},
    JobStatus.PROCESSING: {
        JobStatus.REMUXING,
        JobStatus.UPLOADING,
        JobStatus.COMPLETED,
        JobStatus.FAILED_RETRYABLE,
        JobStatus.FAILED_FINAL,
        JobStatus.CANCELLED,
    },
    JobStatus.REMUXING: {
        JobStatus.UPLOADING,
        JobStatus.FAILED_RETRYABLE,
        JobStatus.FAILED_FINAL,
        JobStatus.CANCELLED,
    },
    JobStatus.UPLOADING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED_RETRYABLE,
        JobStatus.FAILED_FINAL,
        JobStatus.CANCELLED,
    },
    JobStatus.FAILED_RETRYABLE: {JobStatus.QUEUED, JobStatus.FAILED_FINAL, JobStatus.CANCELLED},
    JobStatus.FAILED_FINAL: set(),
    JobStatus.COMPLETED: set(),
    JobStatus.CANCELLED: set(),
}


@dataclass(slots=True)
class ProcessingJob:
    user_id: UUID
    source_asset_id: UUID
    preset: str
    id: UUID = field(default_factory=uuid4)
    status: JobStatus = JobStatus.CREATED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(self, target: JobStatus) -> None:
        if target not in TRANSITIONS[self.status]:
            raise ValueError(f"Invalid job transition: {self.status} -> {target}")
        self.status = target
        self.updated_at = datetime.now(UTC)
