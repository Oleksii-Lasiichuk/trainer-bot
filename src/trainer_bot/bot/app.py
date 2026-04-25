"""Wire the Telegram Application together."""

from __future__ import annotations

from telegram.ext import AIORateLimiter, Application, ApplicationBuilder

from ..config import Settings, get_settings
from ..llm.agent import Agent
from ..llm.client import GroqChat
from ..storage.db import get_database
from ..utils.logging import configure_logging, get_logger
from .handlers import AppServices, build_handlers, error_handler

log = get_logger(__name__)


async def _post_init(application: Application) -> None:
    services: AppServices = application.bot_data["services"]
    await services.database.create_all()
    log.info("bot.ready", whitelist=services.settings.allowed_telegram_user_ids)


async def _post_shutdown(application: Application) -> None:
    services: AppServices | None = application.bot_data.get("services")
    if services is None:
        return
    await services.database.dispose()


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    database = get_database(settings.database_url)
    groq = GroqChat(settings)
    agent = Agent(settings, groq)
    app_services = AppServices(
        settings=settings,
        database=database,
        agent=agent,
        intervals_timeout=float(settings.request_timeout_seconds),
    )

    builder: ApplicationBuilder = ApplicationBuilder().token(settings.telegram_bot_token)
    try:
        builder = builder.rate_limiter(AIORateLimiter())
    except Exception:  # pragma: no cover — optional feature
        log.warning("telegram.rate_limiter_unavailable")
    application = (
        builder
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    application.bot_data["services"] = app_services
    for handler in build_handlers():
        application.add_handler(handler)
    application.add_error_handler(error_handler)
    return application


def run() -> None:
    app = build_application()
    log.info("bot.start_polling")
    app.run_polling(close_loop=False, drop_pending_updates=True)
