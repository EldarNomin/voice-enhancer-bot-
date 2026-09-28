"""Messenger-specific output; the processing worker never constructs SDK messages."""

from pathlib import Path
from typing import Protocol

import httpx
from aiogram import Bot
from aiogram.types import FSInputFile

from voice_enhancer.domain.profile import Preset
from voice_enhancer.i18n import tr
from voice_enhancer.infrastructure.database import JobRow
from voice_enhancer.infrastructure.max_api import MaxAPIError, MaxClient


class Delivery(Protocol):
    async def send_result(self, job: JobRow, path: Path) -> str: ...
    async def update_status(self, job: JobRow, text: str) -> None: ...


def max_keyboard(buttons: list[tuple[str, str]]) -> dict:
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [{"type": "callback", "text": label, "payload": value}] for label, value in buttons
            ]
        },
    }


def max_presets(job_id: str) -> dict:
    return max_keyboard([(p.value.title(), f"p:{job_id}:{p.value}") for p in Preset])


class TelegramDelivery:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_result(self, job: JobRow, path: Path) -> str:
        from voice_enhancer.bot import result_keyboard

        send = self.bot.send_video if job.kind == "video" else self.bot.send_audio
        message = await send(
            job.telegram_chat_id,
            FSInputFile(path, filename=path.name),
            caption=tr(job.locale, f"{job.kind}_ready"),
            reply_markup=result_keyboard(job.id, job.locale),
            request_timeout=3600,
        )
        return str(message.message_id)

    async def update_status(self, job: JobRow, text: str) -> None:
        if job.status_message_id is not None:
            await self.bot.edit_message_text(
                text=text, chat_id=job.telegram_chat_id, message_id=job.status_message_id
            )


class MaxDelivery:
    def __init__(self, client: MaxClient) -> None:
        self.client = client

    async def send_result(self, job: JobRow, path: Path) -> str:
        keyboard = max_keyboard(
            [
                (tr(job.locale, "another_preset_button"), f"r:{job.id}"),
                (tr(job.locale, "new_file_button"), "new:file"),
            ]
        )
        try:
            return await self.client.send_media(
                job.telegram_chat_id, path, job.kind, tr(job.locale, f"{job.kind}_ready"), keyboard
            )
        except httpx.HTTPError:
            raise MaxAPIError(0, "media_transport_error") from None

    async def update_status(self, job: JobRow, text: str) -> None:
        if job.external_status_id:
            await self.client.edit(job.external_status_id, text)
