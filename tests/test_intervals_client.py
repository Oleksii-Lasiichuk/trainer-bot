from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from trainer_bot.intervals.client import IntervalsClient, _normalize_athlete_id
from trainer_bot.intervals.errors import (
    IntervalsAuthError,
    IntervalsNotFoundError,
    IntervalsRateLimitError,
)


def test_normalize_athlete_id() -> None:
    assert _normalize_athlete_id("i123") == "i123"
    assert _normalize_athlete_id("123") == "i123"
    assert _normalize_athlete_id("  i999  ") == "i999"
    with pytest.raises(ValueError):
        _normalize_athlete_id("   ")


@pytest.mark.asyncio
async def test_get_athlete_auth_header() -> None:
    async with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://intervals.icu/api/v1/athlete/i42").mock(
            return_value=httpx.Response(
                200, json={"id": "i42", "name": "Tester", "timezone": "Europe/Kyiv"}
            )
        )
        async with IntervalsClient("i42", "secret") as ic:
            profile = await ic.get_athlete()
        assert profile.name == "Tester"
        assert profile.timezone == "Europe/Kyiv"
        req = route.calls.last.request
        auth = req.headers["authorization"]
        assert auth.startswith("Basic ")


@pytest.mark.asyncio
async def test_list_activities_params() -> None:
    today = date.today()
    oldest = today - timedelta(days=7)
    payload = [
        {
            "id": "iA1",
            "name": "Morning run",
            "type": "Run",
            "start_date_local": "2026-04-22T07:00:00",
            "distance": 8200,
            "moving_time": 2520,
            "average_heartrate": 148.0,
        }
    ]
    async with respx.mock() as mock:
        route = mock.get("https://intervals.icu/api/v1/athlete/i42/activities").mock(
            return_value=httpx.Response(200, json=payload)
        )
        async with IntervalsClient("i42", "secret") as ic:
            acts = await ic.list_activities(oldest=oldest, newest=today, limit=5)
        assert len(acts) == 1
        assert acts[0].id == "iA1"
        assert acts[0].average_heartrate == 148.0
        assert route.calls.last.request.url.params["oldest"] == oldest.isoformat()
        assert route.calls.last.request.url.params["limit"] == "5"


@pytest.mark.asyncio
async def test_401_raises_auth_error() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i42").mock(
            return_value=httpx.Response(401, text="nope")
        )
        async with IntervalsClient("i42", "bad") as ic:
            with pytest.raises(IntervalsAuthError):
                await ic.get_athlete()


@pytest.mark.asyncio
async def test_404_raises_not_found() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/activity/missing").mock(
            return_value=httpx.Response(404, text="gone")
        )
        async with IntervalsClient("i42", "secret") as ic:
            with pytest.raises(IntervalsNotFoundError):
                await ic.get_activity("missing")


@pytest.mark.asyncio
async def test_429_raises_rate_limit_error() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i42").mock(
            return_value=httpx.Response(429, text="slow down")
        )
        async with IntervalsClient("i42", "secret") as ic:
            with pytest.raises(IntervalsRateLimitError):
                await ic.get_athlete()


@pytest.mark.asyncio
async def test_5xx_retries_then_succeeds() -> None:
    async with respx.mock() as mock:
        route = mock.get("https://intervals.icu/api/v1/athlete/i42").mock(
            side_effect=[
                httpx.Response(503, text="boom"),
                httpx.Response(200, json={"id": "i42", "name": "OK"}),
            ]
        )
        async with IntervalsClient("i42", "secret") as ic:
            profile = await ic.get_athlete()
        assert profile.name == "OK"
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_wellness_parses_nulls_and_fields() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i42/wellness").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "2026-04-22",
                        "restingHR": 48,
                        "hrv": None,
                        "sleepSecs": 27000,
                        "ctl": 62.4,
                        "atl": 70.1,
                    }
                ],
            )
        )
        async with IntervalsClient("i42", "secret") as ic:
            entries = await ic.get_wellness(date(2026, 4, 22), date(2026, 4, 22))
        assert len(entries) == 1
        e = entries[0]
        assert e.restingHR == 48
        assert e.hrv is None
        assert e.sleepSecs == 27000
        assert e.ctl == 62.4
        assert e.atl == 70.1


@pytest.mark.asyncio
async def test_fitness_series_derives_tsb() -> None:
    async with respx.mock() as mock:
        mock.get("https://intervals.icu/api/v1/athlete/i42/wellness").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "2026-04-20", "ctl": 60.0, "atl": 50.0},
                    {"id": "2026-04-21", "ctl": 61.0, "atl": 55.0},
                    {"id": "2026-04-22", "ctl": 62.0, "atl": 70.0},
                ],
            )
        )
        async with IntervalsClient("i42", "secret") as ic:
            series = await ic.get_fitness_and_form(days=3)
        assert len(series.points) == 3
        assert series.points[-1].tsb == pytest.approx(-8.0)
