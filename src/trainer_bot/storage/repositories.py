"""CRUD helpers. Thin and purposeful — no ORM magic leaks to callers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Message, MessageRole, User, UserSetting


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, telegram_id: int) -> User | None:
        return await self.session.get(User, telegram_id)

    async def get_or_create(
        self, telegram_id: int, telegram_username: str | None = None
    ) -> User:
        user = await self.get(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, telegram_username=telegram_username)
            self.session.add(user)
            await self.session.flush()
        elif telegram_username and user.telegram_username != telegram_username:
            user.telegram_username = telegram_username
            await self.session.flush()
        return user

    async def get_settings(self, telegram_id: int) -> UserSetting | None:
        return await self.session.get(UserSetting, telegram_id)

    async def upsert_settings(
        self,
        telegram_id: int,
        *,
        intervals_athlete_id: str | None = None,
        intervals_api_key: str | None = None,
        timezone: str | None = None,
        preferred_units: str | None = None,
    ) -> UserSetting:
        settings = await self.get_settings(telegram_id)
        if settings is None:
            settings = UserSetting(
                telegram_id=telegram_id,
                intervals_athlete_id=intervals_athlete_id,
                intervals_api_key=intervals_api_key,
                timezone=timezone or "Europe/Kyiv",
                preferred_units=preferred_units or "metric",
            )
            self.session.add(settings)
        else:
            if intervals_athlete_id is not None:
                settings.intervals_athlete_id = intervals_athlete_id
            if intervals_api_key is not None:
                settings.intervals_api_key = intervals_api_key
            if timezone is not None:
                settings.timezone = timezone
            if preferred_units is not None:
                settings.preferred_units = preferred_units
        await self.session.flush()
        return settings


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_recent(
        self, telegram_id: int, limit: int = 20
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.telegram_id == telegram_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows: Sequence[Message] = result.scalars().all()
        return list(reversed(rows))  # oldest first for LLM consumption

    async def add_user_message(self, telegram_id: int, content: str) -> Message:
        msg = Message(telegram_id=telegram_id, role=MessageRole.USER, content=content)
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def add_assistant_message(
        self,
        telegram_id: int,
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> Message:
        msg = Message(
            telegram_id=telegram_id,
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls_json=tool_calls,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def add_tool_message(
        self,
        telegram_id: int,
        tool_call_id: str,
        name: str,
        content: str,
    ) -> Message:
        msg = Message(
            telegram_id=telegram_id,
            role=MessageRole.TOOL,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def clear_for_user(self, telegram_id: int) -> int:
        stmt = delete(Message).where(Message.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0
