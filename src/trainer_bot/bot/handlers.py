"""Telegram command and message handlers."""

from __future__ import annotations

import contextlib
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackContext,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ..config import Settings
from ..intervals.client import IntervalsClient
from ..intervals.errors import IntervalsAPIError, IntervalsAuthError
from ..llm.agent import Agent
from ..storage.db import Database
from ..storage.repositories import MessageRepository, UserRepository
from ..utils.logging import get_logger
from .auth import user_allowed
from .formatting import split_message

log = get_logger(__name__)

ASK_ATHLETE_ID, ASK_API_KEY = range(2)

HELP_TEXT = (
    "I'm your training/health advisor. I pull your data from intervals.icu "
    "(which syncs from Garmin + Strava) and answer questions in natural language.\n\n"
    "Setup:\n"
    "  /setkey — provide your intervals.icu athlete ID + API key\n"
    "  /whoami — show stored athlete ID\n"
    "  /reset  — clear chat history\n"
    "  /ping   — check intervals.icu connectivity\n"
    "  /help   — show this message\n\n"
    "Try:\n"
    "  \"what was my last run?\"\n"
    "  \"how was my sleep this week?\"\n"
    "  \"should I run hard tomorrow given my load?\"\n"
    "  \"compare my last two long runs\"\n"
)


# ---------------------------------------------------------------------------
# Dependency injection via bot_data
# ---------------------------------------------------------------------------


class AppServices:
    """Services bundle attached to application.bot_data."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        agent: Agent,
        intervals_timeout: float = 30.0,
    ) -> None:
        self.settings = settings
        self.database = database
        self.agent = agent
        self.intervals_timeout = intervals_timeout


def services(context: CallbackContext) -> AppServices:
    svc = context.application.bot_data.get("services")
    if svc is None:
        raise RuntimeError("AppServices missing from bot_data")
    return svc  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _effective_user_id(update: Update) -> int | None:
    if update.effective_user is None:
        return None
    return update.effective_user.id


async def _reply(update: Update, text: str) -> None:
    if update.effective_chat is None:
        return
    for chunk in split_message(text):
        if not chunk:
            continue
        await update.effective_chat.send_message(chunk, disable_web_page_preview=True)


async def _reject_if_not_allowed(update: Update, settings: Settings) -> bool:
    if not user_allowed(settings, _effective_user_id(update)):
        await _reply(
            update,
            "Sorry, this bot is private. Your Telegram user ID is not in the allowlist.",
        )
        return True
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with svc.database.session_factory() as session:
        repo = UserRepository(session)
        await repo.get_or_create(tg_user.id, tg_user.username)
        await session.commit()
    await _reply(
        update,
        f"Hi {tg_user.first_name or ''}! I'm your training/health assistant.\n\n"
        "Run /setkey to connect your intervals.icu account. Then ask me anything about "
        "your training or wellness data.\n\n"
        "Type /help for more.",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    await _reply(update, HELP_TEXT)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with svc.database.session_factory() as session:
        repo = UserRepository(session)
        settings = await repo.get_settings(tg_user.id)
    if settings is None or not settings.is_configured:
        await _reply(update, "Not configured yet. Run /setkey.")
        return
    await _reply(
        update,
        f"Telegram user: {tg_user.id}\n"
        f"intervals.icu athlete ID: {settings.intervals_athlete_id}\n"
        f"Timezone: {settings.timezone}\n"
        f"Units: {settings.preferred_units}",
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with svc.database.session_factory() as session:
        repo = MessageRepository(session)
        deleted = await repo.clear_for_user(tg_user.id)
        await session.commit()
    await _reply(update, f"Conversation history cleared. Removed {deleted} messages.")


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    tg_user = update.effective_user
    if tg_user is None:
        return
    async with svc.database.session_factory() as session:
        repo = UserRepository(session)
        us = await repo.get_settings(tg_user.id)
    if us is None or not us.is_configured:
        await _reply(update, "Not configured. Run /setkey first.")
        return
    try:
        async with IntervalsClient(
            us.intervals_athlete_id or "",
            us.intervals_api_key or "",
            timeout=svc.intervals_timeout,
        ) as ic:
            profile = await ic.get_athlete()
    except IntervalsAuthError:
        await _reply(
            update,
            "intervals.icu rejected your credentials (401). Re-run /setkey with a fresh key.",
        )
        return
    except IntervalsAPIError as e:
        await _reply(update, f"intervals.icu error: {e}")
        return
    await _reply(
        update,
        "OK. Connected to intervals.icu.\n"
        f"Athlete: {profile.name or '(no name)'} "
        f"(tz={profile.timezone or 'unknown'}, ftp={profile.icu_ftp or 'n/a'}).",
    )


# ---------------------------------------------------------------------------
# /setkey conversation
# ---------------------------------------------------------------------------


async def cmd_setkey_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return ConversationHandler.END
    await _reply(
        update,
        "Let's connect intervals.icu.\n\n"
        "1) Send your athlete ID (looks like `i12345`). "
        "You can find it at intervals.icu → Settings → Developer Settings.\n\n"
        "Type /cancel to abort.",
    )
    return ASK_ATHLETE_ID


async def setkey_receive_athlete(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.message is None or update.message.text is None:
        return ASK_ATHLETE_ID
    athlete_id = update.message.text.strip()
    if not athlete_id or " " in athlete_id or len(athlete_id) > 32:
        await _reply(update, "That doesn't look like a valid athlete ID. Try again or /cancel.")
        return ASK_ATHLETE_ID
    if context.user_data is not None:
        context.user_data["pending_athlete_id"] = athlete_id
    await _reply(
        update,
        "Got it. Now send your API key (generated on the same intervals.icu settings page).\n\n"
        "Tip: after we store it, delete your message with the key from this chat for hygiene.",
    )
    return ASK_API_KEY


async def setkey_receive_api_key(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    svc = services(context)
    if update.message is None or update.message.text is None:
        return ASK_API_KEY
    api_key = update.message.text.strip()
    athlete_id = (context.user_data or {}).get("pending_athlete_id")
    tg_user = update.effective_user
    if not athlete_id or tg_user is None:
        await _reply(update, "Something went wrong. Run /setkey again.")
        return ConversationHandler.END
    if len(api_key) < 8 or " " in api_key:
        await _reply(update, "That key looks wrong. Try again or /cancel.")
        return ASK_API_KEY

    # Verify against intervals.icu
    try:
        async with IntervalsClient(
            athlete_id, api_key, timeout=svc.intervals_timeout
        ) as ic:
            profile = await ic.get_athlete()
    except IntervalsAuthError:
        await _reply(
            update,
            "intervals.icu rejected those credentials (401). "
            "Check the athlete ID + key and try /setkey again.",
        )
        return ConversationHandler.END
    except IntervalsAPIError as e:
        await _reply(update, f"intervals.icu error: {e}. Try again later.")
        return ConversationHandler.END

    async with svc.database.session_factory() as session:
        repo = UserRepository(session)
        await repo.get_or_create(tg_user.id, tg_user.username)
        await repo.upsert_settings(
            tg_user.id,
            intervals_athlete_id=athlete_id,
            intervals_api_key=api_key,
            timezone=profile.timezone or svc.settings.default_timezone,
        )
        await session.commit()

    if context.user_data is not None:
        context.user_data.pop("pending_athlete_id", None)

    await _reply(
        update,
        f"Connected to athlete {profile.name or athlete_id}. "
        "Consider deleting the message containing your API key.\n\n"
        "Ask me something, like \"what was my last activity?\"",
    )
    return ConversationHandler.END


async def setkey_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data is not None:
        context.user_data.pop("pending_athlete_id", None)
    await _reply(update, "Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Free-text message handler → agent
# ---------------------------------------------------------------------------


async def handle_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    svc = services(context)
    if await _reject_if_not_allowed(update, svc.settings):
        return
    if update.message is None or not update.message.text:
        return
    tg_user = update.effective_user
    if tg_user is None:
        return

    text = update.message.text.strip()
    if not text:
        return

    async with svc.database.session_factory() as session:
        repo = UserRepository(session)
        await repo.get_or_create(tg_user.id, tg_user.username)
        us = await repo.get_settings(tg_user.id)
        await session.commit()

    if us is None or not us.is_configured:
        await _reply(
            update,
            "You haven't connected intervals.icu yet. Run /setkey to get started.",
        )
        return

    if update.effective_chat is not None:
        with contextlib.suppress(Exception):
            await update.effective_chat.send_chat_action(ChatAction.TYPING)

    try:
        async with IntervalsClient(
            us.intervals_athlete_id or "",
            us.intervals_api_key or "",
            timeout=svc.intervals_timeout,
        ) as intervals, svc.database.session_factory() as session:
            result = await svc.agent.run(
                session=session,
                user_id=tg_user.id,
                user_message=text,
                intervals=intervals,
                user_timezone=us.timezone or svc.settings.default_timezone,
            )
    except IntervalsAuthError:
        await _reply(
            update,
            "intervals.icu credentials rejected (401). Run /setkey again.",
        )
        return
    except IntervalsAPIError as e:
        log.warning("handler.intervals_error", error=str(e))
        await _reply(update, f"intervals.icu error: {e}")
        return
    except Exception:  # pragma: no cover
        log.exception("handler.unexpected_error", user=tg_user.id)
        await _reply(
            update, "Something broke while processing your request. Try again in a moment."
        )
        return

    await _reply(update, result.text or "(no response)")


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    log.exception(
        "telegram.error",
        error=str(context.error),
        update=repr(update)[:500],
    )
    try:
        if isinstance(update, Update) and update.effective_chat is not None:
            await update.effective_chat.send_message(
                "Something broke. Please try again in a moment."
            )
    except Exception:  # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def build_handlers() -> list[Any]:
    setkey_conv = ConversationHandler(
        entry_points=[CommandHandler("setkey", cmd_setkey_start)],
        states={
            ASK_ATHLETE_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setkey_receive_athlete)
            ],
            ASK_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, setkey_receive_api_key)
            ],
        },
        fallbacks=[CommandHandler("cancel", setkey_cancel)],
        name="setkey",
        persistent=False,
    )
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("help", cmd_help),
        CommandHandler("whoami", cmd_whoami),
        CommandHandler("reset", cmd_reset),
        CommandHandler("ping", cmd_ping),
        setkey_conv,
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
    ]
