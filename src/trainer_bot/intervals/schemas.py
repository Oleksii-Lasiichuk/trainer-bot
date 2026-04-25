"""Pydantic models for intervals.icu responses.

Fields modeled tolerantly: intervals.icu returns many nullable fields depending on
which wearable the athlete uses and what Garmin exposes to intervals.icu's sync.
Every optional field defaults to None so missing fields don't crash the client.

The real schema is large; we pick the subset that is actually useful to the LLM.
Extra fields are preserved via ``model_config = ConfigDict(extra="ignore")`` so the
client works even if intervals.icu adds new fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class AthleteProfile(_Base):
    id: str | None = None
    name: str | None = None
    email: str | None = None
    timezone: str | None = None
    sex: str | None = None
    bio: str | None = None
    city: str | None = None
    country: str | None = None
    website: str | None = None
    icu_weight: float | None = None
    icu_resting_hr: int | None = None
    icu_ftp: int | None = None
    icu_type: str | None = None
    icu_sport_settings: list[dict[str, Any]] | None = None
    sportSettings: list[dict[str, Any]] | None = None


class ActivitySummary(_Base):
    id: str
    name: str | None = None
    type: str | None = None
    start_date_local: datetime | None = None
    start_date: datetime | None = None
    distance: float | None = None  # meters
    moving_time: int | None = None  # seconds
    elapsed_time: int | None = None
    total_elevation_gain: float | None = None  # meters
    average_heartrate: float | None = None
    max_heartrate: float | None = None
    average_speed: float | None = None  # m/s
    max_speed: float | None = None
    average_watts: float | None = None
    max_watts: float | None = None
    average_cadence: float | None = None
    kilojoules: float | None = None
    icu_training_load: float | None = Field(default=None, alias="icu_training_load")
    icu_intensity: float | None = None
    icu_efficiency_factor: float | None = None
    icu_pm_ftp: int | None = None
    icu_ftp: int | None = None
    icu_hr_z1_time: int | None = None
    icu_hr_z2_time: int | None = None
    icu_hr_z3_time: int | None = None
    icu_hr_z4_time: int | None = None
    icu_hr_z5_time: int | None = None
    perceivedExertion: float | None = None
    feel: int | None = None
    notes: str | None = None


class ActivityDetail(ActivitySummary):
    description: str | None = None
    gear: dict[str, Any] | None = None
    laps: list[dict[str, Any]] | None = None
    intervals: list[dict[str, Any]] | None = None
    icu_power_hr: float | None = None
    icu_variability_index: float | None = None
    icu_joules: float | None = None
    icu_weighted_avg_watts: float | None = None
    calories: float | None = None
    trainer: bool | None = None
    commute: bool | None = None
    icu_average_watts: float | None = None
    icu_normalized_watts: float | None = None


class WellnessEntry(_Base):
    id: date | None = None  # intervals.icu returns wellness keyed by YYYY-MM-DD date
    weight: float | None = None
    restingHR: int | None = Field(default=None, alias="restingHR")
    hrv: float | None = None
    hrvSDNN: float | None = None
    sleepSecs: int | None = None
    sleepScore: float | None = None
    sleepQuality: float | None = None
    steps: int | None = None
    stress: float | None = None
    fatigue: float | None = None
    soreness: float | None = None
    mood: float | None = None
    spO2: float | None = None
    respiration: float | None = None
    readiness: float | None = None
    bloodPressureSystolic: float | None = None
    bloodPressureDiastolic: float | None = None
    bloodGlucose: float | None = None
    hydration: float | None = None
    menstrualPhase: str | None = None
    menstrualPhasePredicted: str | None = None
    kcalConsumed: float | None = None
    bodyFat: float | None = None
    abdomen: float | None = None
    vo2max: float | None = None
    lactate: float | None = None

    # Derived load metrics (atl/ctl/rampRate may or may not be present in wellness payload
    # depending on the intervals.icu plan + sync status)
    atl: float | None = None
    ctl: float | None = None
    rampRate: float | None = None
    ctlLoad: float | None = None
    atlLoad: float | None = None


class FitnessPoint(_Base):
    """One day in the CTL/ATL/TSB timeseries."""

    date: date
    ctl: float | None = None  # fitness (~42d EWMA)
    atl: float | None = None  # fatigue (~7d EWMA)
    tsb: float | None = None  # form = ctl - atl
    ramp_rate: float | None = None


class FitnessSeries(_Base):
    points: list[FitnessPoint]
    oldest: date
    newest: date


ActivityStreams = dict[str, list[float]]
