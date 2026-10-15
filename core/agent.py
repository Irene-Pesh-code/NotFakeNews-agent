"""Агент поиска первоисточника.

Модель только принимает решения (что искать, что открыть, куда идти, когда остановиться).
Все факты определяет код: загрузка страниц, даты, проверка цитат, атрибуции, граф, лимиты,
выбор первоисточника и уровень уверенности проверяются здесь.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .attributions import Attribution, find_attributions
from .facts import match_facts
from .fetch import FetchError, fetch_telegram_post, fetch_web_page
from .llm import LLM, LLMError, parse_json
from .models import AgentResult, Confidence, InputKind, InputPost, SourceRef
from .prompts import AGENT_SYSTEM, CLAIMS_SYSTEM, TOOLS
from .quotes import STRENGTH, MatchStatus, QuoteMatch, verify_quote
from .search import SearchError, SearchProvider, SearchResult
from .urls import TG_POST_RE, TG_PRIVATE_RE, canonical_url, domain

log = logging.getLogger(__name__)
Progress = Callable[[str], Awaitable[None]]


@dataclass
class Limits:
    max_steps: int = 16            # обращений к модели в цикле
    max_searches: int = 6          # запросов к Tavily
    max_pages: int = 25            # загруженных страниц
    max_depth: int = 6             # шагов назад по атрибуциям
    results_per_search: int = 6
    time_budget: float = 120.0     # секунд на всю проверку


@dataclass
class Claim:
    id: str
    text: str
    kind: str = "утверждение"


@dataclass
class Node:
    id: str
    url: str | None
    title: str | None
    published_at: datetime | None
    date_has_time: bool
    date_source: str | None
    depth: int
    found_via: str                       # input / search / attribution
    text: str = field(default="", repr=False)
    matches: dict[str, QuoteMatch] = field(default_factory=dict)
    attributions: list[Attribution] = field(default_factory=list)

    @property
    def strength(self) -> int:
        """3 — дословно, 2 — близко, 1 — совпадают факты, 0 — не подтверждено."""
        return max((STRENGTH[m.status] for m in self.matches.values()), default=0)

    @property
    def verified(self) -> bool:
        return self.strength > 0

    @property
    def exact(self) -> bool:
        return any(m.status == MatchStatus.EXACT for m in self.matches.values())

    @property
    def ts(self) -> datetime | None:
        dt = self.published_at
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @property
    def date_reliable(self) -> bool:
        return (self.published_at is not None and self.date_has_time
                and not (self.date_source or "").startswith("эвристика"))

    def best_match(self) -> QuoteMatch | None:
        ok = [m for m in self.matches.values() if m.verified]
        return max(ok, key=lambda m: (STRENGTH[m.status], m.score), default=None)


@dataclass
class Edge:
    src: str      # узел, который ссылается
    dst: str      # узел, на который ссылаются (более ранний)
    kind: str     # link — явная ссылка
    label: str = ""


def _clip(s: str | None, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_date(n: Node) -> str:
    if n.published_at is None:
        return "дата не определена"
    if not n.date_has_time:
        d = n.published_at.strftime("%d.%m.%Y")
    elif n.published_at.tzinfo:
        d = n.published_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    else:
        d = n.published_at.strftime("%d.%m.%Y %H:%M")
    return f"{d} ({n.date_source or 'источник даты неизвестен'})"


def _clearly_earlier(a: Node, b: Node) -> bool:
    """a точно опубликован раньше b (с учётом того, что у даты может не быть времени)."""
    if a.ts is None or b.ts is None:
        return False
    slack = timedelta(days=1) if not (a.date_has_time and b.date_has_time) else timedelta(0)
    return a.ts < b.ts - slack


class Agent:
    def __init__(self, http: httpx.AsyncClient, llm: LLM, search: SearchProvider,
                 limits: Limits | None = None, graphs_dir: str = "data/graphs"):
        self.http = http
        self.llm = llm
        self.search = search
        self.limits = limits or Limits()
        self.graphs_dir = Path(graphs_dir)

    async def run(self, post: InputPost, progress: Progress | None = None) -> AgentResult:
        return await _Run(self, post, progress).execute()


class _Run:
    def __init__(self, agent: Agent, post: InputPost, progress: Progress | None):
        self.a = agent
        self.lim = agent.limits
        self.post = post
        self.progress = progress
        self.claims: list[Claim] = []
        self.queries_hint: list[str] = []
        self.nodes: dict[str, Node] = {}
        self.by_url: dict[str, str] = {}
        self.edges: list[Edge] = []
        self.results: dict[str, SearchResult] = {}
        self.result_by_url: dict[str, str] = {}
        self.searches = self.pages = self.steps = 0
        self.finished = False
        self.finish_id: str | None = None
        self.finish_reason = ""
        self.notes: list[str] = []
        self.trace: list[str] = []
        self.deadline = time.monotonic() + self.lim.time_budget

    # ---------------- общий ход ----------------

    async def execute(self) -> AgentResult:
        await self._say("Выделяю утверждения и цитаты…")
        await self._extract_claims()
        self._add_root()
        try:
            await self._loop()
        except LLMError as e:
            log.warning("LLM error: %s", e)
            self.notes.append("модель перестала отвечать, поиск прерван")
        result = self._build_result()
        self._save_graph(result)
        return result

    async def _say(self, text: str) -> None:
        self.trace.append(text)
        if self.progress:
            try:
                await self.progress(text)
            except Exception:  # прогресс не должен ломать проверку
                pass

    # ---------------- шаг 1: цитаты ----------------

    async def _extract_claims(self) -> None:
        items, queries = [], []
        try:
            msg = await self.a.llm.chat([
                {"role": "system", "content": CLAIMS_SYSTEM},
                {"role": "user", "content": self.post.text[:6000]},
            ])
            data = parse_json(msg.content)
            items = data.get("claims") or []
            queries = data.get("queries") or []
        except (LLMError, ValueError) as e:
            log.warning("claims extraction failed: %s", e)

        for it in items:
            if not isinstance(it, dict):
                continue
            q = str(it.get("text") or "").strip()
            if len(q) < 12:
                continue
            # модель обязана скопировать фрагмент дословно — проверяем кодом
            if verify_quote(self.post.text, q).status != MatchStatus.EXACT:
                log.info("отброшен фрагмент, которого нет во входном тексте: %r", q)
                continue
            self.claims.append(Claim(f"q{len(self.claims) + 1}", q, str(it.get("kind") or "утверждение")))
            if len(self.claims) == 3:
                break

        if not self.claims:
            self.claims = self._fallback_claims()
        self.queries_hint = [str(q) for q in queries if isinstance(q, str)][:3]

    def _fallback_claims(self) -> list[Claim]:
        """Если модель не справилась — берём первые содержательные предложения текста."""
        sentences = re.split(r"(?<=[.!?…])\s+", " ".join(self.post.text.split()))
        good = [s for s in sentences if 40 <= len(s) <= 250][:2]
        if not good:
            good = [_clip(self.post.text, 200).rstrip("…")]
        return [Claim(f"q{i}", s) for i, s in enumerate(good, 1)]

    # ---------------- граф ----------------

    def _register(self, node: Node, *urls: str) -> None:
        self.nodes[node.id] = node
        for u in urls:
            if u:
                self.by_url[canonical_url(u)] = node.id

    def _new_id(self) -> str:
        return f"node_{len(self.nodes) + 1}"

    def _check(self, node: Node) -> None:
        """Сначала дословная проверка. Для утверждений (не прямой речи) — ещё и проверка фактов."""
        for c in self.claims:
            m = verify_quote(node.text, c.text)
            if not m.verified and c.kind != "цитата" and node.found_via != "input":
                fm = match_facts(node.text, c.text)
                if fm:
                    m = QuoteMatch(MatchStatus.FACTS, fm.score, fm.fragment, fm.start, fm.end)
            node.matches[c.id] = m

    def _add_root(self) -> None:
        p = self.post
        node = Node(id="node_1", url=p.url,
                    title=p.source_name or "входное сообщение",
                    published_at=p.published_at, date_has_time=p.date_has_time,
                    date_source=p.date_source, depth=0, found_via="input", text=p.text)
        self._check(node)
        node.attributions = find_attributions(p.text, p.links, p.url)
        self._register(node, p.url or "")

    async def _open_url(self, url: str, depth: int, via: str) -> tuple[Node | None, str]:
        key = canonical_url(url)
        if key in self.by_url:
            return self.nodes[self.by_url[key]], "уже открыт ранее"
        if self.pages >= self.lim.max_pages:
            return None, "лимит загрузки страниц исчерпан"
        if TG_PRIVATE_RE.match(url):
            return None, "это закрытый канал Telegram, открыть нельзя"
        self.pages += 1
        try:
            m = TG_POST_RE.match(url)
            page = (await fetch_telegram_post(self.a.http, m.group(1), int(m.group(2))) if m
                    else await fetch_web_page(self.a.http, url))
        except FetchError as e:
            return None, f"не удалось открыть: {e}"
        if not page.text.strip():
            return None, "не удалось извлечь текст страницы"
        if canonical_url(page.url) in self.by_url:     # редирект на уже открытую
            return self.nodes[self.by_url[canonical_url(page.url)]], "уже открыт ранее"

        node = Node(id=self._new_id(), url=page.url, title=page.title,
                    published_at=page.published_at, date_has_time=page.date_has_time,
                    date_source=page.date_source, depth=depth, found_via=via, text=page.text)
        self._check(node)
        node.attributions = find_attributions(page.text, page.links, page.url)
        self._register(node, url, page.url)
        return node, "ok"

    # ---------------- описание для модели ----------------

    def _describe(self, n: Node) -> str:
        where = domain(n.url) if n.url else "без ссылки"
        lines = [f"{n.id} [{where}] {_clip(n.title, 90)}",
                 f"  дата: {_fmt_date(n)}"]
        st = {MatchStatus.EXACT: "найдена точно", MatchStatus.FUZZY: "найдена приблизительно",
              MatchStatus.FACTS: "пересказ — совпадают ключевые факты",
              MatchStatus.NOT_FOUND: "НЕ найдена"}
        lines.append("  цитаты: " + ", ".join(f"{cid} {st[m.status]}" for cid, m in n.matches.items()))
        if not n.verified:
            lines.append("  ⚠ ни цитаты, ни ключевые факты не найдены — страница, скорее всего, не об этой новости")
        if n.attributions:
            lines.append("  атрибуции:")
            for a in n.attributions:
                kind = f"ссылка → {domain(a.url)}" if a.url else "только название, без ссылки"
                mark = ", маркер «как сообщает…»" if a.is_marked else ""
                done = f", пройдено → {a.followed_node}" if a.followed_node else ""
                err = f", не открылась: {a.follow_error}" if a.follow_error else ""
                lines.append(f"    {a.id} «{_clip(a.label, 70)}» ({kind}{mark}{done}{err})")
        else:
            lines.append("  атрибуций нет (текст ни на кого не ссылается)")
        return "\n".join(lines)

    def _budget(self) -> str:
        left = max(0, int(self.deadline - time.monotonic()))
        return (f"\n[осталось: поисков {self.lim.max_searches - self.searches}, "
                f"страниц {self.lim.max_pages - self.pages}, "
                f"шагов {self.lim.max_steps - self.steps}, ~{left} с]")

    def _intro(self) -> str:
        kinds = {
            InputKind.FORWARD_CHANNEL: "пересланный пост Telegram-канала",
            InputKind.FORWARD_OTHER: "пересланное сообщение Telegram",
            InputKind.TELEGRAM_LINK: "пост Telegram по ссылке",
            InputKind.WEB_LINK: "статья по ссылке",
            InputKind.TEXT: "текст, присланный пользователем (источник неизвестен)",
        }
        claims = "\n".join(f"  {c.id} ({c.kind}): «{c.text}»" for c in self.claims)
        hints = ("\nПодсказки для поиска: " + "; ".join(self.queries_hint)) if self.queries_hint else ""
        return (f"Входная публикация — {kinds[self.post.kind]}:\n{_clip(self.post.text, 1500)}\n\n"
                f"Цитаты для проверки:\n{claims}\n\n"
                f"Стартовый узел:\n{self._describe(self.nodes['node_1'])}{hints}{self._budget()}")

    # ---------------- цикл агента ----------------

    async def _loop(self) -> None:
        messages: list[dict] = [{"role": "system", "content": AGENT_SYSTEM},
                                {"role": "user", "content": self._intro()}]
        nudges = 0
        while not self.finished:
            if self.steps >= self.lim.max_steps:
                self.notes.append("исчерпан лимит шагов агента")
                return
            if time.monotonic() > self.deadline:
                self.notes.append("исчерпан лимит времени")
                return
            self.steps += 1
            msg = await self.a.llm.chat(messages, tools=TOOLS)
            calls = msg.tool_calls or []
            assistant = {"role": "assistant", "content": msg.content or ""}
            if calls:
                assistant["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in calls]
            messages.append(assistant)

            if not calls:
                nudges += 1
                if nudges > 2:
                    self.notes.append("модель перестала вызывать инструменты")
                    return
                messages.append({"role": "user", "content":
                                 "Отвечай только вызовом инструмента. Если поиск завершён — вызови finish."})
                continue

            outputs = await self._run_calls(calls)
            for tc in calls:
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": outputs[tc.id]})

    async def _run_calls(self, calls) -> dict[str, str]:
        """search/open/follow из одного ответа модели выполняются параллельно, finish — после них."""
        outputs: dict[str, str] = {}
        parsed = []
        for tc in calls:
            try:
                parsed.append((tc, json.loads(tc.function.arguments or "{}")))
            except json.JSONDecodeError:
                outputs[tc.id] = "Ошибка: аргументы должны быть JSON."

        work = [(tc, a) for tc, a in parsed if tc.function.name != "finish"]
        finals = [(tc, a) for tc, a in parsed if tc.function.name == "finish"]
        results = await asyncio.gather(*(self._dispatch(tc.function.name, a) for tc, a in work),
                                       return_exceptions=True)
        for (tc, _), res in zip(work, results):
            if isinstance(res, Exception):
                log.exception("tool %s failed", tc.function.name, exc_info=res)
                res = f"Внутренняя ошибка инструмента: {res.__class__.__name__}"
            outputs[tc.id] = res
        for tc, a in finals:
            outputs[tc.id] = (await self._dispatch("finish", a)) if not self.finished else "Поиск уже завершён."
        if not self.finished:
            for tc in calls:
                outputs[tc.id] += self._budget()
        return outputs

    async def _dispatch(self, name: str, args: dict) -> str:
        log.info("tool %s %s", name, args)
        if name == "search":
            return await self._t_search(str(args.get("query") or ""))
        if name == "open":
            return await self._t_open(str(args.get("result_id") or ""))
        if name == "follow":
            return await self._t_follow(str(args.get("node_id") or ""), str(args.get("attribution_id") or ""))
        if name == "finish":
            return self._t_finish(str(args.get("origin_node_id") or "none"), str(args.get("reason") or ""))
        return f"Неизвестный инструмент {name}."

    async def _t_search(self, query: str) -> str:
        query = query.strip()
        if not query:
            return "Ошибка: пустой запрос."
        if self.searches >= self.lim.max_searches:
            return "Лимит поисковых запросов исчерпан. Работай с открытыми узлами или вызови finish."
        self.searches += 1
        await self._say(f"Поиск: {_clip(query, 60)}")
        try:
            found = await self.a.search.search(query, max_results=self.lim.results_per_search)
        except SearchError as e:
            return f"Ошибка поиска: {e}"
        if not found:
            return "Ничего не найдено. Попробуй другую формулировку."
        lines = []
        for r in found:
            key = canonical_url(r.url)
            rid = self.result_by_url.get(key)
            if rid is None:
                rid = f"r{len(self.results) + 1}"
                self.results[rid] = r
                self.result_by_url[key] = rid
            opened = f" (уже открыт: {self.by_url[key]})" if key in self.by_url else ""
            hint = f"\n   дата по словам поисковика (не проверена): {r.published_hint}" if r.published_hint else ""
            lines.append(f"{rid} [{domain(r.url)}] {_clip(r.title, 100)}{opened}\n"
                         f"   {_clip(r.snippet, 220)}{hint}")
        return "\n".join(lines)

    async def _t_open(self, rid: str) -> str:
        r = self.results.get(rid.strip())
        if r is None:
            return f"Нет результата {rid}. Используй идентификаторы из результатов поиска."
        await self._say(f"Открываю {domain(r.url)}")
        node, status = await self._open_url(r.url, depth=1, via="search")
        if node is None:
            return f"{rid}: {status}"
        prefix = f"{rid} {status}.\n" if status != "ok" else ""
        return prefix + self._describe(node)

    async def _t_follow(self, node_id: str, att_id: str) -> str:
        node = self.nodes.get(node_id.strip())
        if node is None:
            return f"Нет узла {node_id}."
        att = next((a for a in node.attributions if a.id == att_id.strip()), None)
        if att is None:
            return f"У {node.id} нет атрибуции {att_id}."
        if att.url is None:
            return (f"У атрибуции {att.id} нет ссылки. Найди источник через search, например: "
                    f"«{att.label}» + ключевые слова цитаты.")
        if node.depth + 1 > self.lim.max_depth:
            return "Достигнута максимальная глубина цепочки."
        await self._say(f"Иду по ссылке на {domain(att.url)}")
        child, status = await self._open_url(att.url, depth=node.depth + 1, via="attribution")
        if child is None:
            att.follow_error = status
            return f"{node.id}/{att.id}: {status}"
        att.followed_node = child.id
        if child.id != node.id:
            self.edges.append(Edge(src=node.id, dst=child.id, kind="link", label=_clip(att.label, 80)))
        prefix = f"{status}.\n" if status != "ok" else ""
        return prefix + self._describe(child)

    def _t_finish(self, origin_id: str, reason: str) -> str:
        origin_id = origin_id.strip()
        if origin_id.lower() not in ("none", "null", ""):
            node = self.nodes.get(origin_id)
            if node is None:
                return f"Нет узла {origin_id}. Укажи существующий узел или \"none\"."
            if not node.url:
                return f"У {origin_id} нет ссылки — он не может быть первоисточником. Выбери другой узел или \"none\"."
            if not node.verified:
                return (f"Отклонено: в {origin_id} программа не нашла ни одной цитаты. "
                        f"Выбери узел с найденной цитатой или \"none\".")
            self.finish_id = origin_id
        self.finish_reason = re.sub(r"https?://\S+", "", reason).strip()
        self.finished = True
        return "Поиск завершён."

    # ---------------- итог (только код) ----------------

    def _source(self, n: Node) -> SourceRef:
        m = n.best_match()
        return SourceRef(url=n.url or "", title=n.title, published_at=n.published_at,
                         date_has_time=n.date_has_time, date_source=n.date_source,
                         quote_verified=n.verified and bool(n.url),
                         fragment=m.fragment if m else None,
                         match=m.status.value if m else "exact")

    def _build_result(self) -> AgentResult:
        verified = [n for n in self.nodes.values() if n.url and n.verified]
        dated = [n for n in verified if n.ts is not None]
        earliest = min(dated, key=lambda n: n.ts) if dated else None
        reasons: list[str] = []
        opened = [n for n in self.nodes.values() if n.found_via != "input"]
        stats = {"pages_checked": len(opened),
                 "pages_confirmed": sum(1 for n in opened if n.verified)}

        origin = self.nodes.get(self.finish_id) if self.finish_id else None
        replaced = False
        earlier_weaker: Node | None = None
        if origin:
            # заменить можно только на более раннюю публикацию с не менее сильным подтверждением
            same_or_better = [n for n in dated if n.strength >= origin.strength]
            cand = min(same_or_better, key=lambda n: n.ts) if same_or_better else None
            if cand and _clearly_earlier(cand, origin):
                reasons.append(f"найдена более ранняя публикация ({domain(cand.url)}), "
                               f"первоисточником выбрана она")
                origin, replaced = cand, True
            weaker = [n for n in dated if n.strength < origin.strength and _clearly_earlier(n, origin)]
            earlier_weaker = min(weaker, key=lambda n: n.ts) if weaker else None

        sources = sorted((n for n in verified if n is not origin),
                         key=lambda n: (n.ts is None, n.ts or datetime.max.replace(tzinfo=timezone.utc)))

        if origin is None:
            confidence = Confidence.LOW
            reasons.insert(0, "первоисточник не найден")
            reasons += self.notes
            reached = earliest or (verified[0] if verified else None)
            if reached is None and self.post.url is None:
                reasons.append("у входного сообщения нет ссылки, а подтверждённых публикаций найти не удалось")
            sources = [n for n in sources if n is not reached]   # не показываем дважды
            return AgentResult(origin=None, sources=[self._source(n) for n in sources],
                               confidence=confidence, reason=self._reason(reasons),
                               reached=self._source(reached) if reached else None, **stats)

        issues: list[str] = []
        if origin.strength == STRENGTH[MatchStatus.FACTS]:
            issues.append("текст пересказан: совпадают ключевые факты, но не формулировка")
        elif not origin.exact:
            issues.append("цитата совпала только приблизительно")
        if origin.published_at is None:
            issues.append("дату публикации определить не удалось")
        elif not origin.date_reliable:
            issues.append("дата определена неточно")
        unresolved = [a for a in origin.attributions if a.is_marked and not a.followed_node]
        if unresolved:
            issues.append(f"источник сам ссылается на «{_clip(unresolved[0].label, 40)}», "
                          f"но пройти дальше не удалось")
        if replaced:
            issues.append("выбор агента скорректирован по датам")
        if earlier_weaker:
            issues.append(f"есть более ранняя публикация со схожими фактами ({domain(earlier_weaker.url)}), "
                          f"но без дословного совпадения")

        if issues:
            confidence = Confidence.MEDIUM
            reasons += issues
        else:
            confidence = Confidence.HIGH
            reasons.append(f"цитата найдена дословно, дата — {origin.date_source}, "
                           f"более ранних публикаций с этой цитатой не найдено")
        reasons += self.notes
        return AgentResult(origin=self._source(origin), sources=[self._source(n) for n in sources],
                           confidence=confidence, reason=self._reason(reasons, with_comment=not replaced),
                           **stats)

    def _reason(self, reasons: list[str], with_comment: bool = True) -> str:
        text = "; ".join(reasons)
        text = text[:1].upper() + text[1:] if text else ""
        if self.finish_reason and with_comment:
            text += f". Комментарий агента: {_clip(self.finish_reason, 300)}"
        return text

    # ---------------- граф в JSON (для будущей визуализации) ----------------

    def _save_graph(self, result: AgentResult) -> None:
        def node_json(n: Node) -> dict:
            return {
                "id": n.id, "url": n.url, "title": n.title,
                "published_at": n.published_at.isoformat() if n.published_at else None,
                "date_has_time": n.date_has_time, "date_source": n.date_source,
                "depth": n.depth, "found_via": n.found_via, "verified": n.verified,
                "strength": n.strength,
                "matches": {k: {"status": m.status.value, "score": round(m.score, 1),
                                "fragment": m.fragment} for k, m in n.matches.items()},
                "attributions": [asdict(a) for a in n.attributions],
            }
        data = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": {"kind": self.post.kind.value, "url": self.post.url, "text": self.post.text},
            "claims": [asdict(c) for c in self.claims],
            "nodes": [node_json(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
            "origin": self.finish_id,
            "result": {"origin_url": result.origin.url if result.origin else None,
                       "confidence": result.confidence.value, "reason": result.reason},
            "usage": {"steps": self.steps, "searches": self.searches, "pages": self.pages,
                      "pages_confirmed": result.pages_confirmed},
            "trace": self.trace,
        }
        try:
            self.a.graphs_dir.mkdir(parents=True, exist_ok=True)
            name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + ".json"
            (self.a.graphs_dir / name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except OSError as e:
            log.warning("не удалось сохранить граф: %s", e)
