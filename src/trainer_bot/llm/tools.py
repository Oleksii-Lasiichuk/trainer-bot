"""LLM tool definitions — OpenAI function-calling schemas + dispatch.

Each tool wraps one or more methods of IntervalsClient and formats the payload for the LLM:
- trimmed fields (token cost matters on Groq free tier)
- human-readable units (km, minutes, bpm, min/km pace)
- explicit nulls, not silent omissions (so the LLM doesn't invent values)
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..intervals.client import IntervalsClient
from ..intervals.errors import IntervalsAPIError
from ..intervals.schemas import ActivitySummary, WellnessEntry
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ToolContext:
    intervals: IntervalsClient
    user_id: int
    user_timezone: str = "Europe/Kyiv"


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        return None
    return round(float(value), digits)


def _format_pace_min_per_km(avg_speed_m_s: float | None) -> str | None:
    if not avg_speed_m_s or avg_speed_m_s <= 0:
        return None
    seconds_per_km = 1000.0 / avg_speed_m_s
    minutes = int(seconds_per_km // 60)
    seconds = int(round(seconds_per_km - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


def _activity_summary_to_llm(a: ActivitySummary) -> dict[str, Any]:
    start = a.start_date_local or a.start_date
    distance_km = _round((a.distance or 0) / 1000.0, 2) if a.distance else None
    duration_min = _round((a.moving_time or 0) / 60.0, 1) if a.moving_time else None
    pace = _format_pace_min_per_km(a.average_speed) if a.type and "run" in a.type.lower() else None
    speed_kmh = (
        _round((a.average_speed or 0) * 3.6, 1) if a.average_speed and not pace else None
    )
    return {
        "id": a.id,
        "date": start.date().isoformat() if start else None,
        "time": start.strftime("%H:%M") if start else None,
        "type": a.type,
        "name": a.name,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "avg_hr_bpm": _round(a.average_heartrate, 0),
        "max_hr_bpm": _round(a.max_heartrate, 0),
        "avg_pace_min_per_km": pace,
        "avg_speed_kmh": speed_kmh,
        "avg_watts": _round(a.average_watts, 0),
        "elevation_gain_m": _round(a.total_elevation_gain, 0),
        "training_load": _round(a.icu_training_load, 1),
        "intensity_pct": _round(a.icu_intensity, 1) if a.icu_intensity is not None else None,
        "feel_1to5": a.feel,
        "rpe": a.perceivedExertion,
        "notes": a.notes,
    }


def _wellness_to_llm(w: WellnessEntry) -> dict[str, Any]:
    return {
        "date": w.id.isoformat() if w.id else None,
        "sleep_hours": _round((w.sleepSecs or 0) / 3600.0, 2) if w.sleepSecs else None,
        "sleep_score": _round(w.sleepScore, 0),
        "resting_hr_bpm": w.restingHR,
        "hrv": _round(w.hrv, 1) if w.hrv is not None else _round(w.hrvSDNN, 1),
        "readiness": _round(w.readiness, 0),
        "stress": _round(w.stress, 0),
        "steps": w.steps,
        "weight_kg": _round(w.weight, 2),
        "spo2_pct": _round(w.spO2, 1),
        "respiration": _round(w.respiration, 1),
        "mood": w.mood,
        "soreness": w.soreness,
        "fatigue": w.fatigue,
        "ctl_fitness": _round(w.ctl if w.ctl is not None else w.ctlLoad, 1),
        "atl_fatigue": _round(w.atl if w.atl is not None else w.atlLoad, 1),
        "ramp_rate": _round(w.rampRate, 2),
    }


def _now_in_tz(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _tool_get_current_date_and_time(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    now = _now_in_tz(ctx.user_timezone)
    return {
        "timezone": ctx.user_timezone,
        "iso_datetime": now.isoformat(),
        "date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "week_number": int(now.strftime("%V")),
    }


async def _tool_get_athlete_profile(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    a = await ctx.intervals.get_athlete()
    return {
        "name": a.name,
        "timezone": a.timezone,
        "ftp_watts": a.icu_ftp,
        "resting_hr_bpm": a.icu_resting_hr,
        "weight_kg": _round(a.icu_weight, 2),
        "sex": a.sex,
        "country": a.country,
    }


async def _tool_get_recent_activities(
    ctx: ToolContext, days: int = 7, limit: int = 10
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 50))
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)
    acts = await ctx.intervals.list_activities(oldest=oldest, newest=newest, limit=limit)
    return {
        "range": {"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        "count": len(acts),
        "activities": [_activity_summary_to_llm(a) for a in acts],
    }


async def _tool_get_activity_detail(
    ctx: ToolContext, activity_id: str
) -> dict[str, Any]:
    a = await ctx.intervals.get_activity(str(activity_id))
    base = _activity_summary_to_llm(a)
    base.update(
        {
            "description": a.description,
            "calories": _round(a.calories, 0),
            "normalized_power_w": _round(a.icu_normalized_watts or a.icu_weighted_avg_watts, 0),
            "variability_index": _round(a.icu_variability_index, 2),
            "hr_zones_seconds": {
                "z1": a.icu_hr_z1_time,
                "z2": a.icu_hr_z2_time,
                "z3": a.icu_hr_z3_time,
                "z4": a.icu_hr_z4_time,
                "z5": a.icu_hr_z5_time,
            },
            "lap_count": len(a.laps) if a.laps else 0,
            "interval_count": len(a.intervals) if a.intervals else 0,
        }
    )
    return base


async def _tool_search_activities_by_type(
    ctx: ToolContext, sport_type: str, days: int = 30, limit: int = 20
) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 50))
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)
    acts = await ctx.intervals.list_activities(
        oldest=oldest, newest=newest, limit=min(limit * 3, 150)
    )
    needle = sport_type.strip().lower()
    filtered = [a for a in acts if a.type and needle in a.type.lower()]
    filtered = filtered[:limit]
    return {
        "range": {"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        "sport_type_filter": sport_type,
        "count": len(filtered),
        "activities": [_activity_summary_to_llm(a) for a in filtered],
    }


async def _tool_get_wellness_range(ctx: ToolContext, days: int = 7) -> dict[str, Any]:
    days = max(1, min(int(days), 180))
    newest = date.today()
    oldest = newest - timedelta(days=days - 1)
    entries = await ctx.intervals.get_wellness(oldest, newest)
    entries.sort(key=lambda e: e.id or date.min)
    return {
        "range": {"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        "count": len(entries),
        "wellness": [_wellness_to_llm(e) for e in entries],
    }


async def _tool_get_wellness_today(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    w = await ctx.intervals.get_wellness_today()
    if w is None:
        return {"available": False, "message": "No wellness record found for today or the past 3 days."}
    return {"available": True, **_wellness_to_llm(w)}


async def _tool_get_fitness_trend(ctx: ToolContext, days: int = 42) -> dict[str, Any]:
    days = max(7, min(int(days), 180))
    series = await ctx.intervals.get_fitness_and_form(days=days)
    points = [
        {
            "date": p.date.isoformat(),
            "ctl_fitness": _round(p.ctl, 1),
            "atl_fatigue": _round(p.atl, 1),
            "tsb_form": _round(p.tsb, 1),
            "ramp_rate": _round(p.ramp_rate, 2),
        }
        for p in series.points
    ]
    latest = points[-1] if points else None
    return {
        "range": {"oldest": series.oldest.isoformat(), "newest": series.newest.isoformat()},
        "count": len(points),
        "latest": latest,
        "series": points,
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


ToolFn = Callable[..., Awaitable[dict[str, Any]]]


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_date_and_time",
            "description": (
                "Return the current date, time, weekday and ISO week number in the user's "
                "timezone. Call at the start of a conversation when the user uses relative "
                "dates like 'today', 'this week', 'yesterday'."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_athlete_profile",
            "description": (
                "Return the athlete's intervals.icu profile: name, timezone, FTP, resting HR, "
                "weight, sex, country. Useful for zone calculations and baseline metrics."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activities",
            "description": (
                "List activities (runs, rides, swims, etc.) from the last N days. Returns a "
                "trimmed summary per activity: date, type, distance, duration, heart rate, pace, "
                "training load."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Window size in days. Default 7.",
                        "minimum": 1,
                        "maximum": 365,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of activities to return. Default 10.",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_detail",
            "description": (
                "Fetch full detail for one activity by id: HR zones, normalized power, "
                "variability index, lap / interval count, description, calories. Use after "
                "get_recent_activities identified the activity id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "activity_id": {
                        "type": "string",
                        "description": "intervals.icu activity id (e.g. 'i1234567').",
                    }
                },
                "required": ["activity_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_activities_by_type",
            "description": (
                "List activities in the last N days filtered by sport type (e.g. 'Run', "
                "'Ride', 'Swim'). Case-insensitive substring match on the activity type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sport_type": {
                        "type": "string",
                        "description": "Sport type substring to match, e.g. 'run'.",
                    },
                    "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
                "required": ["sport_type"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wellness_range",
            "description": (
                "Return daily wellness records for the last N days: sleep, resting HR, HRV, "
                "readiness, stress, steps, weight, plus CTL/ATL load metrics when available. "
                "Fields may be null if the wearable did not push them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 1, "maximum": 180, "default": 7}
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_wellness_today",
            "description": (
                "Return today's wellness record (sleep, HRV, resting HR, readiness, stress, "
                "etc.). Falls back to the most recent record in the last 3 days if today is "
                "not yet populated."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fitness_trend",
            "description": (
                "Return the CTL (fitness), ATL (fatigue) and TSB (form = CTL - ATL) time "
                "series for the last N days. Use for training-load context: positive TSB means "
                "freshness, negative means fatigue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 7, "maximum": 180, "default": 42}
                },
                "additionalProperties": False,
            },
        },
    },
]


_DISPATCH: dict[str, ToolFn] = {
    "get_current_date_and_time": _tool_get_current_date_and_time,
    "get_athlete_profile": _tool_get_athlete_profile,
    "get_recent_activities": _tool_get_recent_activities,
    "get_activity_detail": _tool_get_activity_detail,
    "search_activities_by_type": _tool_search_activities_by_type,
    "get_wellness_range": _tool_get_wellness_range,
    "get_wellness_today": _tool_get_wellness_today,
    "get_fitness_trend": _tool_get_fitness_trend,
}


def tool_names() -> list[str]:
    return list(_DISPATCH.keys())


async def dispatch_tool(
    name: str, arguments: dict[str, Any], ctx: ToolContext
) -> dict[str, Any]:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown_tool:{name}", "available": tool_names()}
    try:
        safe_args = arguments or {}
        log.info("tool.dispatch", tool=name, args=safe_args, user=ctx.user_id)
        return await fn(ctx, **safe_args)
    except IntervalsAPIError as e:
        log.warning("tool.intervals_error", tool=name, error=str(e), status=e.status_code)
        return {
            "error": "intervals_api_error",
            "message": str(e),
            "status_code": e.status_code,
        }
    except TypeError as e:
        # bad args from the LLM
        log.warning("tool.bad_args", tool=name, error=str(e))
        return {"error": "bad_arguments", "message": str(e)}
    except Exception as e:  # pragma: no cover — defensive
        log.exception("tool.unexpected_error", tool=name)
        return {"error": "internal", "message": str(e)}
