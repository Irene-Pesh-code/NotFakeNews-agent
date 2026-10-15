"""Ручная проверка без Telegram.

  python -m scripts.try_tools llm                         # проверить подключение к модели
  python -m scripts.try_tools search "текст запроса"
  python -m scripts.try_tools page https://example.com/news
  python -m scripts.try_tools verify https://example.com/news "цитата"
  python -m scripts.try_tools run "текст новости или ссылка"   # полный прогон агента
"""
import argparse
import asyncio
import logging
import os
import re

import httpx
from dotenv import load_dotenv

from core.attributions import find_attributions
from core.fetch import USER_AGENT, fetch_telegram_post, fetch_web_page
from core.models import InputKind, InputPost
from core.quotes import verify_quote
from core.search import TavilySearch
from core.urls import TG_POST_RE


async def load_page(http, url):
    if m := TG_POST_RE.match(url):
        return await fetch_telegram_post(http, m.group(1), int(m.group(2)))
    return await fetch_web_page(http, url)


async def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("llm")
    s = sub.add_parser("search"); s.add_argument("query")
    g = sub.add_parser("page"); g.add_argument("url")
    v = sub.add_parser("verify"); v.add_argument("url"); v.add_argument("quote")
    r = sub.add_parser("run"); r.add_argument("input")
    args = p.parse_args()

    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as http:
        if args.cmd == "llm":
            from core.config import load_settings
            from core.llm import LLM
            st = load_settings(require_bot=False)
            llm = LLM(st.llm_base_url, st.llm_api_key, st.llm_model)
            msg = await llm.chat([{"role": "user", "content": "Ответь одним словом: работает?"}])
            print(f"Модель {st.llm_model} ответила: {msg.content}")

        elif args.cmd == "search":
            tavily = TavilySearch(http, os.getenv("TAVILY_API_KEY", ""))
            for i, res in enumerate(await tavily.search(args.query), 1):
                print(f"{i}. {res.title}\n   {res.url}\n   дата от поисковика: {res.published_hint}\n"
                      f"   {res.snippet[:200]}\n")

        elif args.cmd == "page":
            page = await load_page(http, args.url)
            print(f"URL: {page.url}\nЗаголовок: {page.title}")
            print(f"Дата: {page.published_at} (источник: {page.date_source})")
            print("Атрибуции:")
            for a in find_attributions(page.text, page.links, page.url):
                print(f"  {a.id} {'[маркер] ' if a.is_marked else ''}«{a.label}» → {a.url or 'без ссылки'}")
            print(f"\n{page.text[:1500]}")

        elif args.cmd == "verify":
            page = await load_page(http, args.url)
            m = verify_quote(page.text, args.quote)
            print(f"Статус: {m.status.value}, похожесть: {m.score:.1f}")
            if m.fragment:
                print(f"Фрагмент: {m.fragment}")

        else:
            from core.config import load_settings
            from core.factory import build_agent
            from core.ingest import ingest_link
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
            logging.getLogger("httpx").setLevel(logging.WARNING)
            agent = build_agent(load_settings(require_bot=False), http)
            text = args.input.strip()
            if re.fullmatch(r"https?://\S+", text):
                post = await ingest_link(text, http)
            else:
                post = InputPost(kind=InputKind.TEXT, text=text)

            async def progress(msg):
                print(f"… {msg}")

            res = await agent.run(post, progress=progress)
            print("\n=== РЕЗУЛЬТАТ ===")
            if res.origin:
                print(f"Первоисточник: {res.origin.url}\nДата: {res.origin.published_at}\n"
                      f"Фрагмент: {res.origin.fragment}")
            else:
                print("Первоисточник не найден." +
                      (f" Поиск дошёл до: {res.reached.url}" if res.reached else ""))
            print(f"Уверенность: {res.confidence.value}\nПричина: {res.reason}")
            for i, src in enumerate([s for s in res.sources if s.quote_verified][:10], 1):
                print(f"  {i}. {src.url} — {src.published_at}")
            print("\nГраф сохранён в папку data/graphs/")


if __name__ == "__main__":
    asyncio.run(main())
