import asyncio
import logging
import os

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from core.config import load_settings
from core.factory import build_agent
from core.fetch import USER_AGENT

from .handlers import router


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    session = AiohttpSession(proxy=os.getenv("TELEGRAM_PROXY") or None)
    bot = Bot(settings.bot_token, session=session,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async with httpx.AsyncClient(timeout=settings.http_timeout,
                                 headers={"User-Agent": USER_AGENT}) as http:
        agent = build_agent(settings, http)
        # http, settings и agent передаются в хендлеры по имени параметра
        dp = Dispatcher(http=http, settings=settings, agent=agent)
        dp.include_router(router)
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
