"""Сборка ответа пользователю. Здесь же — последняя линия защиты:
ссылки без подтверждённой цитаты в ответ не попадают."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from core.models import AgentResult, Confidence, InputPost, SourceRef

MAX_SOURCES = 10
MAX_FRAGMENT = 400
TG_LIMIT = 4096


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def fmt_date(dt: datetime | None, has_time: bool = True) -> str:
    if dt is None:
        return "дата не определена"
    if not has_time:
        return dt.strftime("%d.%m.%Y")
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return dt.strftime("%d.%m.%Y %H:%M (часовой пояс не указан)")


MATCH_LABEL = {"exact": "", "fuzzy": " · близкое совпадение", "facts": " · пересказ, совпадают факты"}


def _link(src: SourceRef) -> str:
    label = _clip(src.title, 80) if src.title else _domain(src.url)
    return f'<a href="{escape(src.url, quote=True)}">{escape(label)}</a> ({escape(_domain(src.url))})'


def format_result(post: InputPost, result: AgentResult) -> str:
    sources = [s for s in result.sources if s.quote_verified][:MAX_SOURCES]
    origin = result.origin if result.origin and result.origin.quote_verified else None

    confidence = result.confidence
    if origin is None and confidence == Confidence.HIGH:
        confidence = Confidence.LOW  # без первоисточника высокая уверенность невозможна

    parts: list[str] = []
    if origin:
        parts.append(f"<b>✅ Первоисточник:</b> {_link(origin)}")
        parts.append(f"<b>Дата:</b> {fmt_date(origin.published_at, origin.date_has_time)}")
        if origin.match == "facts":
            parts.append("<i>Текст пересказан: совпадают ключевые факты, но не формулировка.</i>")
        if origin.fragment:
            parts.append(f"<b>Фрагмент:</b>\n<blockquote>{escape(_clip(origin.fragment, MAX_FRAGMENT))}</blockquote>")
    else:
        parts.append("<b>❌ Первоисточник не найден.</b>")
        r = result.reached
        if r and r.quote_verified:
            parts.append(f"Поиск дошёл до: {_link(r)}, {fmt_date(r.published_at, r.date_has_time)}")
        elif r:
            name = escape(r.title or _domain(r.url))
            parts.append(f"Поиск дошёл до: {name} (ссылку не показываю — цитата там не подтверждена)")

    if result.pages_checked:
        parts.append(f"\nПроверено страниц: {result.pages_checked}, "
                     f"новость подтвердилась на {result.pages_confirmed}.")
    parts.append(f"\n<b>Уверенность:</b> {confidence.value}")
    parts.append(f"<b>Причина:</b> {escape(result.reason)}")

    text = "\n".join(parts)
    if sources:
        header = f"\n\n<b>Источники ({len(sources)}):</b>"
        lines = [f"{i}. {_link(s)} — {fmt_date(s.published_at, s.date_has_time)}{MATCH_LABEL.get(s.match, '')}"
                 for i, s in enumerate(sources, 1)]
        block = header
        for line in lines:
            if len(text) + len(block) + len(line) + 1 > TG_LIMIT - 50:
                block += "\n…список сокращён"
                break
            block += "\n" + line
        text += block
    return text
