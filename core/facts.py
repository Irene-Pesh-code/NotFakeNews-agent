"""Проверка пересказа: совпадают ли ключевые факты утверждения с фрагментом страницы.

Нужна для новостей, которые разные СМИ пишут своими словами. Дословной цитаты нет,
но в одном месте страницы (окно из нескольких соседних предложений) должны совпасть:
- все числа из утверждения — точно;
- не меньше 75% имён собственных (люди, места, организации, аббревиатуры);
- не меньше 60% значимых слов (70%, если ни имён, ни чисел в утверждении нет).
Слова сравниваются по упрощённой основе, чтобы «митинга» и «митинге» совпадали.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WINDOW = 3                 # сколько соседних предложений считаем одним местом
MIN_CONTENT_WORDS = 4      # слишком короткие утверждения так не проверяем
ENTITY_SHARE = 0.75
CONTENT_SHARE = 0.60
CONTENT_SHARE_NO_ANCHORS = 0.70
MAX_FRAGMENT = 700

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SENT_RE = re.compile(r"[^.!?…\n]+(?:[.!?…]+|$)")
_STOP = set("""
который которая которое которые которых также более менее после перед через между около
этого этому этом этой этих такой такие такая такое может могут будет будут были было была
является являются года году годах сегодня вчера завтра время однако кроме потому поэтому
только очень всего всех если когда where which their about after there these those would
""".split())


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    n = len(w)
    if n > 7:
        return w[:6]
    if n >= 6:
        return w[: n - 2]
    if n == 5:
        return w[:4]
    return w


def _num(s: str) -> str:
    return s.replace(",", ".")


@dataclass
class ClaimFacts:
    numbers: list[str]
    entities: set[str]     # основы имён собственных
    content: set[str]      # основы значимых слов


def extract_facts(text: str) -> ClaimFacts:
    numbers = [_num(n) for n in _NUM_RE.findall(text)]
    entities, content = set(), set()
    sentence_start = True
    for m in re.finditer(r"[.!?…]+|[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*", text):
        tok = m.group(0)
        if not tok[0].isalpha():
            sentence_start = True
            continue
        is_abbr = len(tok) >= 2 and tok.isupper()
        if is_abbr or (tok[0].isupper() and not sentence_start):
            entities.add(stem(tok))
        if len(tok) >= 5 and tok.lower() not in _STOP:
            content.add(stem(tok))
        sentence_start = False
    return ClaimFacts(numbers, entities, content)


@dataclass
class FactMatch:
    score: float           # доля совпавших значимых слов, 0–100
    fragment: str
    start: int
    end: int


def match_facts(page_text: str, claim: str) -> FactMatch | None:
    facts = extract_facts(claim)
    if len(facts.content) < MIN_CONTENT_WORDS:
        return None
    need = CONTENT_SHARE if (facts.entities or facts.numbers) else CONTENT_SHARE_NO_ANCHORS

    sents = [(m.start(), m.end(), m.group(0)) for m in _SENT_RE.finditer(page_text) if m.group(0).strip()]
    info = [({stem(w) for w in _WORD_RE.findall(s)}, {_num(n) for n in _NUM_RE.findall(s)})
            for _, _, s in sents]

    best: FactMatch | None = None
    for i in range(len(sents)):
        j = min(len(sents), i + WINDOW)
        stems = set().union(*(info[k][0] for k in range(i, j)))
        nums = set().union(*(info[k][1] for k in range(i, j)))
        if any(n not in nums for n in facts.numbers):
            continue
        if facts.entities and len(facts.entities & stems) / len(facts.entities) < ENTITY_SHARE:
            continue
        share = len(facts.content & stems) / len(facts.content)
        if share < need:
            continue
        if best is None or share * 100 > best.score:
            start, end = sents[i][0], sents[j - 1][1]
            frag = " ".join(page_text[start:end].split())
            if len(frag) > MAX_FRAGMENT:
                frag = frag[:MAX_FRAGMENT - 1] + "…"
            best = FactMatch(share * 100, frag, start, end)
    return best
