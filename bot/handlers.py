from __future__ import annotations

import asyncio
import logging
import time
from html import escape

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import LinkPreviewOptions, Message

from core.agent import Agent
from core.config import Settings
from core.ingest import IngestError, ingest_message

from .formatting import format_result

log = logging.getLogger(__name__)
router = Router()

HELP = (
    "Я ищу первоисточник новости.\n\n"
    "Что можно прислать:\n"
    "• переслать пост из Telegram-канала;\n"
    "• ссылку на публичный пост t.me или на статью;\n"
    "• текст новости или цитату.\n\n"
    "В ответ: первоисточник, дата, фрагмент с цитатой, до 10 источников "
    "и уровень уверенности. Показываю только ссылки, в которых цитата "
    "реально найдена в тексте страницы."
)

_in_progress: set[int] = set()  # пользователи, у которых идёт проверка


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message((F.text & ~F.text.startswith("/")) | F.caption)
async def handle_news(message: Message, http: httpx.AsyncClient, settings: Settings,
                      agent: Agent) -> None:
    user_id = message.from_user.id if message.from_user else message.chat.id
    if user_id in _in_progress:
        await message.reply("Предыдущая проверка ещё идёт, подождите немного.")
        return

    _in_progress.add(user_id)
    status = await message.reply("🔎 Разбираю сообщение…")

    last_edit = 0.0

    async def progress(text: str) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if now - last_edit < 2.5:      # не чаще раза в 2.5 с, иначе Telegram ограничит
            return
        last_edit = now
        try:
            await status.edit_text(f"🔎 {escape(text)}…")
        except TelegramBadRequest:
            pass

    try:
        try:
            post = await ingest_message(message, http)
        except IngestError as e:
            await status.edit_text(f"⚠️ {escape(str(e))}")
            return

        log.info("user=%s kind=%s url=%s", user_id, post.kind.value, post.url)
        await status.edit_text("🔎 Ищу первоисточник… Это может занять до пары минут.")

        try:
            result = await asyncio.wait_for(agent.run(post, progress=progress),
                                            timeout=settings.agent_timeout)
        except asyncio.TimeoutError:
            await status.edit_text("⏱ Не уложился в лимит времени. Попробуйте ещё раз позже.")
            return

        await status.edit_text(format_result(post, result),
                               link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception:
        log.exception("Ошибка при обработке сообщения")
        try:
            await status.edit_text("Произошла внутренняя ошибка. Попробуйте ещё раз.")
        except TelegramBadRequest:
            pass
    finally:
        _in_progress.discard(user_id)


@router.message()
async def unsupported(message: Message) -> None:
    await message.reply("Я понимаю только текст, пересланные посты и ссылки. /help — подробнее.")
