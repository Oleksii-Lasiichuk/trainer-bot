"""Async HTTP client for intervals.icu REST API.

Auth: HTTP Basic, username = the literal string "API_KEY", password = user's generated key.
Base URL: https://intervals.icu/api/v1/
Docs:    https://intervals.icu/api/v1/docs/swagger-ui/index.html
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..utils.logging import get_logger
from .errors import (
    IntervalsAPIError,
    IntervalsAuthError,
    IntervalsNotFoundError,
    IntervalsRateLimitError,
    IntervalsServerError,
)
from .schemas import (
    ActivityDetail,
    ActivityStreams,
    ActivitySummary,
    AthleteProfile,
    FitnessPoint,
    FitnessSeries,
    WellnessEntry,
)

log = get_logger(__name__)

DEFAULT_BASE_URL = "https://intervals.icu/api/v1"
_BASIC_AUTH_USERNAME = "API_KEY"


def _normalize_athlete_id(athlete_id: str) -> str:
    """intervals.icu athlete IDs are stringy like 'i12345'. Trim whitespace + enforce prefix."""
    s = athlete_id.strip()
    if not s:
        raise ValueError("athlete_id is empty")
    # Numeric-only input → prepend 'i'. Anything else pass through.
    if not s.startswith("i") and s.isdigit():
        s = f"i{s}"
    return s


class IntervalsClient:
    """Async wrapper over intervals.icu. One instance per user request."""

    def __init__(
        self,
        athlete_id: str,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.athlete_id = _normalize_athlete_id(athlete_id)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._external_client = client is not None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            auth=(_BASIC_AUTH_USERNAME, api_key),
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "trainer-bot/0.1"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._external_client:
            await self._client.aclose()

    # ---- low-level --------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async for attempt in AsyncRetrying(
            reraise=True,
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((IntervalsServerError, httpx.TransportError)),
        ):
            with attempt:
                try:
                    resp = await self._client.request(method, path, **kwargs)
                except httpx.TransportError:
                    raise
                self._raise_for_status(resp)
                if resp.status_code == 204 or not resp.content:
                    return None
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    return resp.json()
                return resp.text

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:500]
        if resp.status_code == 401:
            raise IntervalsAuthError(
                "intervals.icu rejected credentials (401). Check athlete ID and API key.",
                status_code=401,
            )
        if resp.status_code == 404:
            raise IntervalsNotFoundError(
                f"intervals.icu 404: {resp.request.url}", status_code=404
            )
        if resp.status_code == 429:
            raise IntervalsRateLimitError(
                f"intervals.icu rate-limited (429): {body}", status_code=429
            )
        if resp.status_code >= 500:
            raise IntervalsServerError(
                f"intervals.icu {resp.status_code}: {body}", status_code=resp.status_code
            )
        raise IntervalsAPIError(
            f"intervals.icu {resp.status_code}: {body}", status_code=resp.status_code
        )

    # ---- methods ---------------------------------------------------------

    async def get_athlete(self) -> AthleteProfile:
        data = await self._request("GET", f"/athlete/{self.athlete_id}")
        return AthleteProfile.model_validate(data or {})

    async def list_activities(
        self,
        oldest: date | None = None,
        newest: date | None = None,
        limit: int = 20,
    ) -> list[ActivitySummary]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
        if oldest:
            params["oldest"] = oldest.isoformat()
        if newest:
            params["newest"] = newest.isoformat()
        data = await self._request(
            "GET", f"/athlete/{self.athlete_id}/activities", params=params
        )
        if not isinstance(data, list):
            return []
        return [ActivitySummary.model_validate(item) for item in data]

    async def get_activity(self, activity_id: str) -> ActivityDetail:
        data = await self._request("GET", f"/activity/{activity_id}")
        return ActivityDetail.model_validate(data or {})

    async def get_wellness(
        self,
        oldest: date,
        newest: date,
    ) -> list[WellnessEntry]:
        params = {"oldest": oldest.isoformat(), "newest": newest.isoformat()}
        data = await self._request(
            "GET", f"/athlete/{self.athlete_id}/wellness", params=params
        )
        if not isinstance(data, list):
            return []
        return [WellnessEntry.model_validate(item) for item in data]

    async def get_wellness_today(self) -> WellnessEntry | None:
        """Most recent wellness record on/before today."""
        today = date.today()
        entries = await self.get_wellness(today - timedelta(days=3), today)
        if not entries:
            return None
        # intervals.icu returns records with `id` = ISO date; pick the latest.
        entries.sort(key=lambda e: e.id or date.min, reverse=True)
        return entries[0]

    async def get_fitness_and_form(self, days: int = 42) -> FitnessSeries:
        newest = date.today()
        oldest = newest - timedelta(days=max(days - 1, 1))
        wellness = await self.get_wellness(oldest, newest)
        points: list[FitnessPoint] = []
        for entry in wellness:
            ctl = entry.ctl if entry.ctl is not None else entry.ctlLoad
            atl = entry.atl if entry.atl is not None else entry.atlLoad
            tsb = (ctl - atl) if (ctl is not None and atl is not None) else None
            if entry.id is None:
                continue
            points.append(
                FitnessPoint(
                    date=entry.id,
                    ctl=ctl,
                    atl=atl,
                    tsb=tsb,
                    ramp_rate=entry.rampRate,
                )
            )
        points.sort(key=lambda p: p.date)
        return FitnessSeries(points=points, oldest=oldest, newest=newest)

    async def get_activity_streams(
        self, activity_id: str, types: list[str]
    ) -> ActivityStreams:
        params = {"types": ",".join(types)}
        data = await self._request(
            "GET", f"/activity/{activity_id}/streams", params=params
        )
        result: ActivityStreams = {}
        if isinstance(data, list):
            # intervals.icu streams endpoint returns list[{type, data: []}]
            for stream in data:
                t = stream.get("type")
                d = stream.get("data")
                if isinstance(t, str) and isinstance(d, list):
                    result[t] = [float(x) if x is not None else float("nan") for x in d]
        elif isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    result[k] = [float(x) if x is not None else float("nan") for x in v]
        return result
