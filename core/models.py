"""Структуры данных. Все поля заполняет код, а не модель."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass
class Link:
    """Ссылка из текста публикации вместе с окружающим текстом."""
    url: str
    anchor: str = ""     # текст ссылки
    context: str = ""    # текст вокруг ссылки (для поиска «как сообщает…»)


class InputKind(str, Enum):
    FORWARD_CHANNEL = "forward_channel"  # переслан пост канала
    FORWARD_OTHER = "forward_other"      # переслано от пользователя / из чата
    TELEGRAM_LINK = "telegram_link"      # ссылка на публичный пост t.me
    WEB_LINK = "web_link"                # ссылка на сайт
    TEXT = "text"                        # просто текст новости / цитата


@dataclass
class InputPost:
    """Входная публикация, которую прислал пользователь."""
    kind: InputKind
    text: str
    url: str | None = None
    source_name: str | None = None
    published_at: datetime | None = None
    date_has_time: bool = True
    date_source: str | None = None       # откуда код взял дату
    links: list[Link] = field(default_factory=list)


class Confidence(str, Enum):
    HIGH = "высокий"
    MEDIUM = "средний"
    LOW = "низкий"


@dataclass
class SourceRef:
    """Источник, проверенный кодом."""
    url: str
    title: str | None = None
    published_at: datetime | None = None
    date_has_time: bool = True
    date_source: str | None = None
    quote_verified: bool = False         # цитата или факты найдены кодом в тексте страницы
    fragment: str | None = None          # фрагмент текста вокруг найденного места
    match: str = "exact"                 # exact / fuzzy / facts — насколько точно совпало


@dataclass
class AgentResult:
    origin: SourceRef | None             # первоисточник (если найден)
    sources: list[SourceRef]             # источники исходной новости
    confidence: Confidence
    reason: str
    reached: SourceRef | None = None     # докуда дошёл поиск, если первоисточник не найден
    pages_checked: int = 0               # сколько страниц открыто (кроме входной)
    pages_confirmed: int = 0             # на скольких из них новость подтвердилась
