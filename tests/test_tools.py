from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from trainer_bot.intervals.client import IntervalsClient
from trainer_bot.intervals.schemas import ActivitySummary, WellnessEntry
from trainer_bot.llm.tools import (
    ToolContext,
    _activity_summary_to_llm,
    _format_pace_min_per_km,
    _wellness_to_llm,
    dispatch_tool,
    tool_names,
)


def test_pace_format() -> None:
    # 3.0 m/s ~ 5:33/km
    assert _format_pace_min_per_km(3.0) == "5:33"
    assert _format_pace_min_per_km(None) is None
    assert _format_pace_min_per_km(0) is None


def test_activity_summary_to_llm() -> None:
    a = ActivitySummary(
        id="iA",
        name="Long run",
        type="Run",
        start_date_local=datetime(2026, 4, 22, 7, 15, tzinfo=UTC),
        distance=10000,
        moving_time=3000,
        average_heartrate=150.0,
        average_speed=3.33,
        icu_training_load=75.0,
        icu_intensity=82.5,
    )
    out = _activity_summary_to_llm(a)
    assert out["distance_km"] == 10.0
    assert out["duration_min"] == 50.0
    assert out["avg_hr_bpm"] == 150
    assert out["avg_pace_min_per_km"] == "5:00"
    assert out["training_load"] == 75.0


def test_wellness_to_llm_null_safe() -> None:
    from datetime import date

    w = WellnessEntry(id=date(2026, 4, 22), sleepSecs=None, restingHR=None, hrv=None)
    out = _wellness_to_llm(w)
    assert out["sleep_hours"] is None
    assert out["resting_hr_bpm"] is None
    assert out["hrv"] is None


def test_tool_names_full_registry() -> None:
    names = tool_names()
    assert {
        "get_current_date_and_time",
        "get_athlete_profile",
        "get_recent_activities",
        "get_activity_detail",
        "search_activities_by_type",
        "get_wellness_range",
        "get_wellness_today",
        "get_fitness_trend",
    }.issubset(set(names))


@pytest.mark.asyncio
async def test_dispatch_unknown_tool() -> None:
    async with IntervalsClient("i1", "k") as ic:
        ctx = ToolContext(intervals=ic, user_id=1, user_timezone="UTC")
        result = await dispatch_tool("no_such_tool", {}, ctx)
        assert result["error"] == "unknown_tool:no_such_tool"


@pytest.mark.asyncio
async def test_dispatch_get_current_date_and_time() -> None:
    async with IntervalsClient("i1", "k") as ic:
        ctx = ToolContext(intervals=ic, user_id=1, user_timezone="Europe/Kyiv")
        result = await dispatch_tool("get_current_date_and_time", {}, ctx)
        assert "iso_datetime" in result
        assert result["timezone"] == "Europe/Kyiv"


@pytest.mark.asyncio
async def test_dispatch_recent_activities_formats() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i1/activities").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "iA",
                        "name": "Run",
                        "type": "Run",
                        "start_date_local": "2026-04-22T07:00:00",
                        "distance": 5000,
                        "moving_time": 1500,
                        "average_speed": 3.33,
                        "average_heartrate": 150,
                    }
                ],
            )
        )
        async with IntervalsClient("i1", "k") as ic:
            ctx = ToolContext(intervals=ic, user_id=1)
            result = await dispatch_tool(
                "get_recent_activities", {"days": 3, "limit": 2}, ctx
            )
    assert result["count"] == 1
    assert result["activities"][0]["distance_km"] == 5.0


@pytest.mark.asyncio
async def test_dispatch_maps_intervals_error() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i1").mock(
            return_value=httpx.Response(401, text="no")
        )
        async with IntervalsClient("i1", "k") as ic:
            ctx = ToolContext(intervals=ic, user_id=1)
            result = await dispatch_tool("get_athlete_profile", {}, ctx)
    assert result["error"] == "intervals_api_error"
    assert result["status_code"] == 401
