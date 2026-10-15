"""Поиск атрибуций: мест, где текст ссылается на другой источник.
«как сообщает РБК», «по данным Минфина», «со ссылкой на ТАСС», ссылки в тексте."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Link
from .urls import canonical_url, domain

MAX_LINKS = 12
MAX_NAMES = 6

# маркеры «текст пересказывает чужое сообщение»
MARKER_RE = re.compile(
    r"(как\s+сообща\w+|как\s+пиш\w+|по\s+данным|по\s+информации|со\s+ссылкой\s+на|"
    r"переда[её]т|передают|сообщает|сообщают|сообщил[аио]?\s+в|пишет|пишут|"
    r"об\s+этом\s+сообщ\w+|первым\s+сообщ\w+|источник|подробнее|"
    r"according\s+to|reported\s+by|citing|via|source)",
    re.IGNORECASE,
)

# «как сообщает РБК», «по данным «Коммерсанта»», «со ссылкой на Reuters»
NAMED_RE = re.compile(
    r"(?i:как\s+сообща(?:ет|ют)|как\s+пиш(?:ет|ут)|по\s+данным|по\s+информации|"
    r"со\s+ссылкой\s+на|переда[её]т|передают)\s+"
    r"(«[^»]{2,60}»|\"[^\"]{2,60}\"|[A-ZА-ЯЁ][\w.\-]*(?:\s+[A-ZА-ЯЁ\d][\w.\-]*){0,3})"
)

# служебные ссылки, которые атрибуцией не являются
_SKIP_DOMAINS = ("t.me/share", "telegram.me/share", "vk.com/share", "twitter.com/intent",
                 "x.com/intent", "facebook.com/sharer")


@dataclass
class Attribution:
    id: str
    label: str                    # текст ссылки или название источника
    url: str | None               # None — источник назван, но ссылки нет
    context: str
    is_marked: bool               # рядом есть маркер «как сообщает…»
    followed_node: str | None = None
    follow_error: str | None = None


def find_attributions(text: str, links: list[Link], page_url: str | None) -> list[Attribution]:
    own = canonical_url(page_url) if page_url else None
    own_domain = domain(page_url) if page_url else None

    link_atts: list[Attribution] = []
    seen: set[str] = set()
    for link in links:
        if any(s in link.url for s in _SKIP_DOMAINS):
            continue
        key = canonical_url(link.url)
        if key == own or key in seen:
            continue
        marked = bool(MARKER_RE.search(link.context))
        # внутренние ссылки сайта («читайте также») берём только с маркером
        if own_domain and domain(link.url) == own_domain and not marked:
            continue
        seen.add(key)
        label = link.anchor if link.anchor and link.anchor != link.url else domain(link.url)
        link_atts.append(Attribution("", label, link.url, link.context, marked))
    link_atts.sort(key=lambda a: not a.is_marked)       # сначала с маркерами
    link_atts = link_atts[:MAX_LINKS]

    name_atts: list[Attribution] = []
    seen_names: set[str] = {a.label.lower() for a in link_atts}  # уже есть как ссылка
    for m in NAMED_RE.finditer(text):
        name = m.group(1).strip("«»\" ")
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        ctx = " ".join(text[max(0, m.start() - 100): m.end() + 100].split())
        name_atts.append(Attribution("", name, None, ctx, True))
        if len(name_atts) >= MAX_NAMES:
            break

    result = link_atts + name_atts
    for i, a in enumerate(result, 1):
        a.id = f"a{i}"
    return result
