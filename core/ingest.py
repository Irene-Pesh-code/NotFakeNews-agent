"""Разбор входящего сообщения: пересланный пост, ссылка или текст."""
from __future__ import annotations

import re

import httpx
from aiogram.types import (Message, MessageOriginChannel, MessageOriginChat,
                           MessageOriginHiddenUser, MessageOriginUser)

from .fetch import FetchError, context_around, fetch_telegram_post, fetch_web_page
from .models import InputKind, InputPost, Link
from .urls import TG_POST_RE, TG_PRIVATE_RE

URL_RE = re.compile(r"https?://[^\s<>\"«»]+", re.IGNORECASE)

# Если кроме ссылки в сообщении меньше стольких символов — считаем, что прислали ссылку.
LINK_ONLY_THRESHOLD = 25


class IngestError(Exception):
    """Ошибка разбора; текст показывается пользователю."""


def _fix_url(u: str) -> str:
    u = u.rstrip(".,;:!?)»")
    return u if re.match(r"^https?://", u, re.IGNORECASE) else "https://" + u


def extract_links(message: Message) -> list[Link]:
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    links: list[Link] = []
    seen: set[str] = set()

    def add(url: str, anchor: str, start: int) -> None:
        url = _fix_url(url)
        if url in seen:
            return
        seen.add(url)
        ctx = context_around(text, start, start + len(anchor)) if start >= 0 else ""
        links.append(Link(url=url, anchor=anchor.strip(), context=ctx))

    for e in entities:
        if e.type in ("text_link", "url"):
            anchor = e.extract_from(text)
            url = e.url if e.type == "text_link" else anchor
            if url:
                add(url, anchor, text.find(anchor))
    for m in URL_RE.finditer(text):
        add(m.group(0), m.group(0), m.start())
    return links


def _origin_name(origin) -> str | None:
    if isinstance(origin, MessageOriginUser):
        return origin.sender_user.full_name
    if isinstance(origin, MessageOriginHiddenUser):
        return origin.sender_user_name
    if isinstance(origin, MessageOriginChat):
        return origin.sender_chat.title
    return None


async def ingest_message(message: Message, http: httpx.AsyncClient) -> InputPost:
    text = (message.text or message.caption or "").strip()
    links = extract_links(message)
    origin = message.forward_origin

    # 1. Пересланное сообщение: дата и источник берутся из метаданных Telegram.
    if origin is not None:
        if not text:
            raise IngestError("В пересланном сообщении нет текста. Перешлите пост с текстом или подписью.")
        if isinstance(origin, MessageOriginChannel):
            chat = origin.chat
            url = f"https://t.me/{chat.username}/{origin.message_id}" if chat.username else None
            return InputPost(kind=InputKind.FORWARD_CHANNEL, text=text, url=url,
                             source_name=chat.title or chat.username,
                             published_at=origin.date, date_source="дата исходного поста Telegram",
                             links=links)
        return InputPost(kind=InputKind.FORWARD_OTHER, text=text,
                         source_name=_origin_name(origin), published_at=origin.date,
                         date_source="дата пересланного сообщения Telegram", links=links)

    if not text:
        raise IngestError("Пришлите текст новости, перешлите пост из канала или отправьте ссылку.")

    # 2. Сообщение — по сути только ссылка.
    if links and len(URL_RE.sub("", text).strip()) < LINK_ONLY_THRESHOLD:
        return await ingest_link(links[0].url, http)

    # 3. Просто текст (возможно, со ссылками внутри).
    return InputPost(kind=InputKind.TEXT, text=text, links=links)


async def ingest_link(url: str, http: httpx.AsyncClient) -> InputPost:
    if TG_PRIVATE_RE.match(url):
        raise IngestError("Это ссылка на пост закрытого канала, открыть её я не могу. "
                          "Перешлите сам пост в этот чат.")
    try:
        if m := TG_POST_RE.match(url):
            page = await fetch_telegram_post(http, m.group(1), int(m.group(2)))
            kind = InputKind.TELEGRAM_LINK
        else:
            page = await fetch_web_page(http, url)
            kind = InputKind.WEB_LINK
    except FetchError as e:
        raise IngestError(f"Не получилось открыть ссылку: {e}.") from e

    if not page.text.strip():
        raise IngestError("Не удалось извлечь текст новости по ссылке. "
                          "Пришлите текст новости сообщением.")
    return InputPost(kind=kind, text=page.text, url=page.url, source_name=page.title,
                     published_at=page.published_at, date_has_time=page.date_has_time,
                     date_source=page.date_source, links=page.links)
