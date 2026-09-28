"""Durable webhook inbox. PostgreSQL is the acceptance boundary, not an in-memory task."""

import hashlib
import json
from datetime import timedelta

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from voice_enhancer.infrastructure.database import Base, JobStore, utcnow

SUPPORTED_UPDATES = {"message_created", "message_callback", "bot_started"}


class MaxInboxRow(Base):
    __tablename__ = "max_inbox"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    event_timestamp: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    available_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


def event_key(payload: dict) -> str:
    kind = payload["update_type"]
    if not isinstance(payload.get("timestamp"), int):
        raise TypeError("Missing event timestamp")
    if kind == "message_created":
        identity = [kind, payload["message"]["body"]["mid"]]
    elif kind == "message_callback":
        identity = [kind, payload["callback"]["callback_id"]]
    elif kind == "bot_started":
        identity = [kind, payload["chat_id"], payload["user"]["user_id"], payload["timestamp"]]
    else:
        raise ValueError("Unsupported update")
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


class MaxInbox:
    def __init__(self, store: JobStore) -> None:
        self.sessions = store.sessions

    async def accept(self, payload: dict) -> bool:
        key = event_key(payload)
        async with self.sessions() as session:
            kind = payload["update_type"]
            actor = (
                payload["callback"]["user"]
                if kind == "message_callback"
                else payload["user"]
                if kind == "bot_started"
                else payload["message"].get("sender") or {}
            )
            session.add(
                MaxInboxRow(
                    id=key,
                    payload=payload,
                    actor_id=actor.get("user_id"),
                    event_timestamp=payload["timestamp"],
                )
            )
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                if await session.get(MaxInboxRow, key) is None:
                    raise
                return False

    async def recover(self) -> None:
        # Called only under the single MAX ingress lock on the shared volume.
        async with self.sessions.begin() as session:
            await session.execute(
                update(MaxInboxRow)
                .where(MaxInboxRow.status == "processing")
                .values(status="pending")
            )

    async def claim(self) -> MaxInboxRow | None:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(MaxInboxRow)
                .where(MaxInboxRow.status == "pending", MaxInboxRow.available_at <= utcnow())
                .order_by(MaxInboxRow.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.attempts >= 5:
                row.status = "failed"
                row.payload = None
                return None
            row.status = "processing"
            row.attempts += 1
            return row

    async def finish(self, key: str, *, success: bool) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(MaxInboxRow, key)
            if row is None:
                return
            if success or row.attempts >= 5:
                row.status = "done" if success else "failed"
                row.payload = None  # Discard temporary download URLs and message text.
            else:
                row.status = "pending"
                row.available_at = utcnow() + timedelta(seconds=min(2**row.attempts, 60))

    async def cleanup(self) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                delete(MaxInboxRow).where(
                    MaxInboxRow.status.in_(["done", "failed"]),
                    MaxInboxRow.created_at < utcnow() - timedelta(days=7),
                )
            )
