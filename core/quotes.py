"""Проверка кодом, что цитата действительно есть в тексте страницы.

1. Текст и цитата нормализуются одинаково: регистр, ё→е, кавычки убираются,
   все виды тире → «-», пробелы схлопываются, невидимые символы удаляются.
2. Ищется точное вхождение. Если цитата сокращена многоточием («…», «[...]»),
   части ищутся по порядку.
3. Если точного нет — нечёткое сравнение (rapidfuzz), только для длинных цитат,
   только с высоким порогом и только если все числа совпадают точно.
Фрагмент для ответа вырезается из ИСХОДНОГО текста страницы.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from rapidfuzz import fuzz

FUZZY_THRESHOLD = 90.0   # минимальная похожесть для «близкого совпадения», 0–100
MIN_FUZZY_LEN = 30       # короче — только точное совпадение
CONTEXT = 150            # символов контекста вокруг цитаты во фрагменте
MAX_FRAGMENT = 700

_QUOTE_CHARS = set("\"'«»„“”‟‘’‚‛`´")
_DASHES = set("‐‑‒–—―−")
_INVISIBLE = {"\u00ad", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_ELLIPSIS_RE = re.compile(r"\s*(?:[\[(]\s*(?:\.\.\.|…)\s*[\])]|\.\.\.|…)\s*")


class MatchStatus(str, Enum):
    EXACT = "exact"          # точное совпадение после нормализации
    FUZZY = "fuzzy"          # близкое совпадение выше порога
    FACTS = "facts"          # текст пересказан, но ключевые факты совпадают (core/facts.py)
    NOT_FOUND = "not_found"


# сила подтверждения: чем больше, тем надёжнее
STRENGTH = {MatchStatus.EXACT: 3, MatchStatus.FUZZY: 2, MatchStatus.FACTS: 1, MatchStatus.NOT_FOUND: 0}


@dataclass
class QuoteMatch:
    status: MatchStatus
    score: float
    fragment: str | None = None
    start: int | None = None   # позиция цитаты в исходном тексте
    end: int | None = None

    @property
    def verified(self) -> bool:
        return self.status != MatchStatus.NOT_FOUND


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Нормализует текст и возвращает карту: позиция в результате → позиция в оригинале."""
    out: list[str] = []
    pos: list[int] = []
    prev_space = True  # чтобы не было пробела в начале
    for i, ch in enumerate(text):
        if ch in _INVISIBLE or ch in _QUOTE_CHARS:
            continue
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c in _QUOTE_CHARS:
                continue
            if c == "ё":
                c = "е"
            elif c in _DASHES:
                c = "-"
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            pos.append(i)
    if out and out[-1] == " ":
        out.pop()
        pos.pop()
    return "".join(out), pos


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def _fragment(text: str, start: int, end: int) -> str:
    if end - start > MAX_FRAGMENT:
        return text[start:start + MAX_FRAGMENT].rstrip() + "…"
    a, b = max(0, start - CONTEXT), min(len(text), end + CONTEXT)
    # расширяем до границ слов
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    frag = " ".join(text[a:b].split())
    return ("…" if a > 0 else "") + frag + ("…" if b < len(text) else "")


def _result(text: str, pos_map: list[int], ns: int, ne: int,
            status: MatchStatus, score: float) -> QuoteMatch:
    start, end = pos_map[ns], pos_map[ne - 1] + 1
    return QuoteMatch(status, score, _fragment(text, start, end), start, end)


def verify_quote(page_text: str, quote: str) -> QuoteMatch:
    norm_text, pos_map = normalize_with_map(page_text)
    parts = [normalize(p) for p in _ELLIPSIS_RE.split(quote)]
    parts = [p for p in parts if p]
    if not norm_text or not parts:
        return QuoteMatch(MatchStatus.NOT_FOUND, 0.0)

    # 1. Точное совпадение (все части по порядку)
    cursor, spans = 0, []
    for part in parts:
        j = norm_text.find(part, cursor)
        if j < 0:
            break
        spans.append((j, j + len(part)))
        cursor = j + len(part)
    else:
        return _result(page_text, pos_map, spans[0][0], spans[-1][1], MatchStatus.EXACT, 100.0)

    # 2. Нечёткое совпадение — только для достаточно длинных цитат
    joined = " ".join(parts)
    if len(joined) < MIN_FUZZY_LEN or len(joined) > len(norm_text):
        return QuoteMatch(MatchStatus.NOT_FOUND, 0.0)
    al = fuzz.partial_ratio_alignment(joined, norm_text)
    if al is not None and al.score >= FUZZY_THRESHOLD and al.dest_end > al.dest_start:
        # Числа должны совпадать точно: «до 2030 года» и «до 2031 года» почти одинаковы
        # по символам, но это разные факты.
        if sorted(_NUMBER_RE.findall(joined)) != sorted(
                _NUMBER_RE.findall(norm_text[al.dest_start:al.dest_end])):
            return QuoteMatch(MatchStatus.NOT_FOUND, al.score)
        return _result(page_text, pos_map, al.dest_start, al.dest_end, MatchStatus.FUZZY, al.score)
    return QuoteMatch(MatchStatus.NOT_FOUND, al.score if al else 0.0)
