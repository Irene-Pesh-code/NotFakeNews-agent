"""Загрузка страниц и извлечение текста/даты. Только код, без модели."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

import httpx
import trafilatura
from bs4 import BeautifulSoup

from .models import Link

USER_AGENT = "Mozilla/5.0 (compatible; NewsOriginBot/0.1)"
MAX_BYTES = 3_000_000


class FetchError(Exception):
    """Ошибка загрузки; текст понятен пользователю."""


@dataclass
class PageContent:
    url: str
    title: str | None
    text: str
    published_at: datetime | None = None
    date_has_time: bool = True
    date_source: str | None = None
    links: list[Link] = field(default_factory=list)


async def fetch_raw(client: httpx.AsyncClient, url: str) -> tuple[str, bytes]:
    """Возвращает (итоговый URL после редиректов, сырые байты HTML)."""
    try:
        async with client.stream("GET", url, follow_redirects=True) as r:
            if r.status_code >= 400:
                raise FetchError(f"страница вернула ошибку HTTP {r.status_code}")
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype:
                raise FetchError(f"по ссылке не HTML-страница ({ctype or 'тип неизвестен'})")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FetchError("страница слишком большая")
                chunks.append(chunk)
            return str(r.url), b"".join(chunks)
    except httpx.HTTPError as e:
        raise FetchError(f"не удалось загрузить страницу ({e.__class__.__name__})") from e


# ---------- даты ----------

def _parse_iso(value: str) -> tuple[datetime, bool] | None:
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    has_time = "T" in value or " " in value
    return dt, has_time


def _jsonld_dates(soup: BeautifulSoup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                v = x.get("datePublished")
                if isinstance(v, str):
                    yield v
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)


def find_published_date(soup: BeautifulSoup) -> tuple[datetime, bool, str] | None:
    """Ищет дату публикации в разметке. Возвращает (дата, есть_ли_время, источник)."""
    for attr in ("property", "name"):
        for key in ("article:published_time", "og:published_time"):
            tag = soup.find("meta", attrs={attr: key})
            if tag and tag.get("content") and (p := _parse_iso(tag["content"])):
                return p[0], p[1], f"meta {key}"
    tag = soup.find(attrs={"itemprop": "datePublished"})
    if tag:
        value = tag.get("content") or tag.get("datetime")
        if value and (p := _parse_iso(value)):
            return p[0], p[1], "itemprop datePublished"
    for value in _jsonld_dates(soup):
        if p := _parse_iso(value):
            return p[0], p[1], "JSON-LD datePublished"
    return None


# ---------- ссылки ----------

LINK_CONTEXT = 150
_MD_LINK_RE = re.compile(r"\[([^\]]{0,300})\]\((\S+?)\)")


def _clean_ctx(text: str) -> str:
    return " ".join(_MD_LINK_RE.sub(lambda m: m.group(1), text).split())


def _links_from_markdown(md: str, base_url: str) -> list[Link]:
    """Ссылки из основного текста статьи (trafilatura в формате markdown)."""
    links = []
    for m in _MD_LINK_RE.finditer(md):
        if m.start() > 0 and md[m.start() - 1] == "!":   # картинка
            continue
        href = m.group(2).strip("<>")
        if href.startswith(("mailto:", "javascript:", "#", "tel:")):
            continue
        url = urljoin(base_url, href)
        if not url.startswith(("http://", "https://")):
            continue
        ctx = md[max(0, m.start() - LINK_CONTEXT): m.end() + LINK_CONTEXT]
        links.append(Link(url=url, anchor=_clean_ctx(m.group(1)), context=_clean_ctx(ctx)))
    return links


def context_around(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - LINK_CONTEXT): end + LINK_CONTEXT].split())


# ---------- обычные сайты ----------

async def fetch_web_page(client: httpx.AsyncClient, url: str) -> PageContent:
    final_url, raw = await fetch_raw(client, url)
    text = trafilatura.extract(raw, url=final_url, favor_precision=True,
                               include_comments=False, include_tables=False) or ""
    meta = trafilatura.extract_metadata(raw, default_url=final_url)
    soup = BeautifulSoup(raw, "html.parser")

    page = PageContent(url=final_url, title=meta.title if meta else None, text=text)
    try:
        md = trafilatura.extract(raw, url=final_url, output_format="markdown", include_links=True,
                                 favor_precision=True, include_comments=False, include_tables=False)
        page.links = _links_from_markdown(md or "", final_url)
    except Exception:  # старые версии trafilatura без markdown — просто без ссылок
        page.links = []

    found = find_published_date(soup)
    if found:
        page.published_at, page.date_has_time, page.date_source = found
    elif meta and meta.date and (p := _parse_iso(meta.date)):
        page.published_at, page.date_has_time = p[0], False
        page.date_source = "эвристика trafilatura (только день)"
    return page


# ---------- посты Telegram ----------

async def fetch_telegram_post(client: httpx.AsyncClient, channel: str, msg_id: int) -> PageContent:
    """Загружает публичный пост через встраиваемый виджет t.me."""
    canonical = f"https://t.me/{channel}/{msg_id}"
    _, raw = await fetch_raw(client, f"{canonical}?embed=1&mode=tme")
    soup = BeautifulSoup(raw, "html.parser")

    if soup.select_one(".tgme_widget_message_error"):
        raise FetchError("пост не найден: возможно, он удалён или канал закрытый")

    text_el = soup.select_one(".tgme_widget_message_text")
    text, links = "", []
    if text_el:
        for br in text_el.find_all("br"):
            br.replace_with("\n")
        anchors = [(a.get_text(), urljoin(canonical, a["href"])) for a in text_el.find_all("a", href=True)]
        text = text_el.get_text().strip()
        for anchor, url in anchors:
            i = text.find(anchor) if anchor else -1
            ctx = context_around(text, i, i + len(anchor)) if i >= 0 else ""
            links.append(Link(url=url, anchor=anchor.strip(), context=ctx))

    owner = soup.select_one(".tgme_widget_message_owner_name")
    page = PageContent(url=canonical,
                       title=owner.get_text(strip=True) if owner else channel,
                       text=text, links=links)

    time_el = soup.select_one(".tgme_widget_message_date time[datetime]")
    if time_el and (p := _parse_iso(time_el["datetime"])):
        page.published_at, page.date_has_time = p
        page.date_source = "дата поста Telegram"
    return page
