import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    llm_api_key: str
    tavily_api_key: str
    llm_base_url: str = "https://api.orcarouter.ai/v1"
    llm_model: str = "deepseek/deepseek-chat"
    agent_timeout: float = 240.0
    http_timeout: float = 10.0


def load_settings(require_bot: bool = True) -> Settings:
    load_dotenv()
    required = ["LLM_API_KEY", "TAVILY_API_KEY"] + (["BOT_TOKEN"] if require_bot else [])
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Не заданы переменные в .env: {', '.join(missing)} (см. .env.example)")
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        llm_api_key=os.environ["LLM_API_KEY"],
        tavily_api_key=os.environ["TAVILY_API_KEY"],
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.orcarouter.ai/v1"),
        llm_model=os.getenv("LLM_MODEL", "deepseek/deepseek-chat"),
        agent_timeout=float(os.getenv("AGENT_TIMEOUT", "240")),
        http_timeout=float(os.getenv("HTTP_TIMEOUT", "10")),
    )
