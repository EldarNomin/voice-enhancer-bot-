from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from voice_enhancer.domain.job import TRANSITIONS, JobStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (UniqueConstraint("telegram_chat_id", "source_message_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    source_path: Mapped[str] = mapped_column(String(512))
    output_path: Mapped[str | None] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    locale: Mapped[str] = mapped_column(String(2), default="ru", server_default="ru")
    preset: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.CREATED.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_message_id: Mapped[int | None] = mapped_column(BigInteger)
    status_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


async def initialize_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        if connection.dialect.name == "postgresql":
            # Existing jobs predate localization and had a Russian interface.
            await connection.execute(
                text("ALTER TABLE processing_jobs ADD COLUMN IF NOT EXISTS locale VARCHAR(2) NOT NULL DEFAULT 'ru'")
            )


class JobStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def create_pending(
        self,
        *,
        job_id: str,
        chat_id: int,
        user_id: int,
        message_id: int,
        kind: str,
        source_path: Path,
        locale: str = "ru",
    ) -> tuple[str, bool]:
        async with self.sessions() as session:
            session.add(
                JobRow(
                    id=job_id,
                    telegram_chat_id=chat_id,
                    telegram_user_id=user_id,
                    source_message_id=message_id,
                    kind=kind,
                    locale=locale,
                    source_path=str(source_path),
                    status=JobStatus.CREATED.value,
                )
            )
            try:
                await session.commit()
                return job_id, True
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(JobRow.id).where(
                        JobRow.telegram_chat_id == chat_id,
                        JobRow.source_message_id == message_id,
                    )
                )
                if existing is None:
                    raise
                return existing, False

    async def get(self, job_id: str) -> JobRow | None:
        async with self.sessions() as session:
            return await session.get(JobRow, job_id)

    async def queue_for_preset(
        self, job_id: str, user_id: int, preset: str, status_message_id: int | None = None
    ) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.id == job_id,
                    JobRow.telegram_user_id == user_id,
                    JobRow.status == JobStatus.CREATED.value,
                )
                .values(
                    status=JobStatus.QUEUED.value,
                    preset=preset,
                    status_message_id=status_message_id,
                    updated_at=utcnow(),
                )
            )
            return result.rowcount == 1

    async def claim(self, job_id: str) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.status == JobStatus.QUEUED.value)
                .values(
                    status=JobStatus.PROCESSING.value,
                    attempts=JobRow.attempts + 1,
                    updated_at=utcnow(),
                )
            )
            return result.rowcount == 1

    async def transition(
        self,
        job_id: str,
        expected: JobStatus,
        target: JobStatus,
        *,
        output_path: Path | None = None,
        error_code: str | None = None,
        result_message_id: int | None = None,
    ) -> bool:
        if target not in TRANSITIONS[expected]:
            raise ValueError(f"Invalid job transition: {expected} -> {target}")
        values: dict[str, object] = {"status": target.value, "updated_at": utcnow()}
        if output_path is not None:
            values["output_path"] = str(output_path)
        if error_code is not None:
            values["error_code"] = error_code
        if result_message_id is not None:
            values["result_message_id"] = result_message_id
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(JobRow.id == job_id, JobRow.status == expected.value)
                .values(**values)
            )
            return result.rowcount == 1

    async def recover_jobs(self) -> list[str]:
        """For the single-worker deployment, retry interrupted work and missing queue entries."""
        interrupted = [
            JobStatus.PROCESSING.value,
            JobStatus.REMUXING.value,
            JobStatus.UPLOADING.value,
            JobStatus.FAILED_RETRYABLE.value,
        ]
        async with self.sessions.begin() as session:
            await session.execute(
                update(JobRow)
                .where(JobRow.status.in_(interrupted))
                .values(status=JobStatus.QUEUED.value, updated_at=utcnow())
            )
            rows = await session.scalars(
                select(JobRow.id).where(JobRow.status == JobStatus.QUEUED.value)
            )
            return list(rows)

    async def queued_ids(self) -> list[str]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(JobRow.id).where(JobRow.status == JobStatus.QUEUED.value)
            )
            return list(rows)

    async def expire_pending(self, before: datetime) -> list[str]:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(JobRow)
                .where(
                    JobRow.status == JobStatus.CREATED.value,
                    JobRow.created_at < before,
                )
                .values(status=JobStatus.CANCELLED.value, updated_at=utcnow())
                .returning(JobRow.id)
            )
            return list(result.scalars())

    async def finished_before(self, before: datetime) -> list[JobRow]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(JobRow).where(
                    JobRow.status.in_([JobStatus.COMPLETED.value, JobStatus.FAILED_FINAL.value]),
                    JobRow.updated_at < before,
                )
            )
            return list(rows)
