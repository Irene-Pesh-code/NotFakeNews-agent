"""Сборка агента из настроек (общая для бота и консольного скрипта)."""
import httpx

from .agent import Agent, Limits
from .config import Settings
from .llm import LLM
from .search import TavilySearch


def build_agent(settings: Settings, http: httpx.AsyncClient) -> Agent:
    llm = LLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
    search = TavilySearch(http, settings.tavily_api_key)
    # запас 20 с: агент сам завершится и успеет собрать ответ до жёсткого таймаута бота
    limits = Limits(time_budget=max(30.0, settings.agent_timeout - 20))
    return Agent(http, llm, search, limits)
