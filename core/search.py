"""Веб-поиск. Поисковик только подсказывает, ГДЕ искать:
даты и наличие цитаты потом проверяются загрузкой страницы."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from .urls import canonical_url


class SearchError(Exception):
    """Ошибка поиска; текст понятен человеку."""


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    published_hint: str | None = None  # дата от поисковика — только подсказка, не факт


class SearchProvider(Protocol):
    calls: int

    async def search(self, query: str, max_results: int = 8) -> list[SearchResult]: ...


class TavilySearch:
    URL = "https://api.tavily.com/search"
    MAX_QUERY = 400

    def __init__(self, client: httpx.AsyncClient, api_key: str, depth: str = "basic"):
        if not api_key:
            raise SearchError("Не задан TAVILY_API_KEY")
        self.client = client
        self.api_key = api_key
        self.depth = depth   # "basic" = 1 кредит, "advanced" = 2 кредита
        self.calls = 0       # для учёта лимитов

    async def search(self, query: str, max_results: int = 8, topic: str = "general") -> list[SearchResult]:
        payload = {
            "query": query.strip()[: self.MAX_QUERY],
            "search_depth": self.depth,
            "max_results": max_results,
            "topic": topic,               # "general" или "news"
            "include_answer": False,
            "include_raw_content": False,
        }
        self.calls += 1
        try:
            r = await self.client.post(self.URL, json=payload, timeout=25,
                                       headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise SearchError(f"Tavily недоступен ({e.__class__.__name__})") from e

        if r.status_code == 401:
            raise SearchError("Tavily: неверный API-ключ")
        if r.status_code in (429, 432, 433):
            raise SearchError("Tavily: превышен лимит запросов или кредитов")
        if r.status_code >= 400:
            raise SearchError(f"Tavily: ошибка HTTP {r.status_code}: {r.text[:200]}")

        results, seen = [], set()
        for item in r.json().get("results", []):
            url = item.get("url")
            if not url:
                continue
            key = canonical_url(url)
            if key in seen:
                continue
            seen.add(key)
            results.append(SearchResult(url=url, title=item.get("title") or "",
                                        snippet=item.get("content") or "",
                                        published_hint=item.get("published_date")))
        return results
