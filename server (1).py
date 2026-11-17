"""Read-only Garmin Connect MCP server, using Garmin's Personal API key.

Environment variables:
  GARMIN_PERSONAL_API_KEY  Your Personal API key from
                           https://connect.garmin.com/app/settings/personalApiToken
  CONNECTOR_SECRET         A long random string. The MCP endpoint is served at
                           /<CONNECTOR_SECRET>/mcp so only someone who knows the
                           full URL can reach your data.
  PORT                     Port to listen on (default 8000).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from datetime import date, timedelta
from typing import Any

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse

BASE_URL = os.environ.get("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)


class GarminClient:
    """Exchanges the Personal API key for a 24h bearer token and makes GET requests."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=30.0)

    async def _bearer(self, force: bool = False) -> str:
        async with self._lock:
            # Refresh when missing, forced, or within an hour of expiry.
            if force or not self._token or self._expires_at - time.time() < 3600:
                r = await self._http.post(
                    TOKEN_PATH,
                    headers={"Accept": "*/*", "Authorization": f"ApiKey {self._api_key}"},
                )
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"Garmin rejected the Personal API key (HTTP {r.status_code}). "
                        "It may have expired or been deleted; create a new one in Garmin Connect."
                    )
                self._token = r.text.strip()
                self._expires_at = _jwt_exp(self._token)
            return self._token

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(2):
            token = await self._bearer(force=attempt == 1)
            r = await self._http.get(
                path,
                params=params,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            )
            if r.status_code == 401 and attempt == 0:
                continue  # token revoked early: get a fresh one and retry once
            if r.status_code >= 400:
                raise RuntimeError(f"Garmin API error {r.status_code} for {path}: {r.text[:300]}")
            return r.json() if r.content else None


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except Exception:
        return time.time() + 23 * 3600


def _day(value: str | None) -> str:
    """Accept YYYY-MM-DD, 'today' or 'yesterday'; default today."""
    if not value or value == "today":
        return date.today().isoformat()
    if value == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    return date.fromisoformat(value).isoformat()


_client: GarminClient | None = None


def garmin() -> GarminClient:
    global _client
    if _client is None:
        key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GARMIN_PERSONAL_API_KEY is not set on the server.")
        _client = GarminClient(key)
    return _client


mcp = MCPServer(
    name="garmin-connect",
    title="Garmin Connect (personal)",
    instructions=(
        "Read-only access to the owner's Garmin Connect data: activities (runs, rides, "
        "strength, golf), daily heart rate, sleep, HRV, VO2 max and daily summaries. "
        "Dates are YYYY-MM-DD. Use list_recent_activities first to find activity IDs."
    ),
)


def _slim_activity(a: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "activityId", "activityName", "startTimeLocal", "duration", "movingDuration",
        "distance", "elevationGain", "averageSpeed", "averageHR", "maxHR", "calories",
        "aerobicTrainingEffect", "anaerobicTrainingEffect", "trainingEffectLabel",
        "activityTrainingLoad", "avgPower", "normPower", "averageRunningCadenceInStepsPerMinute",
        "vO2MaxValue", "totalSets", "totalReps", "steps",
    ]
    out = {k: a[k] for k in keys if a.get(k) is not None}
    t = a.get("activityType")
    if isinstance(t, dict):
        out["activityType"] = t.get("typeKey")
    return out


@mcp.tool(annotations=READ_ONLY)
async def list_recent_activities(
    days: int = 14, limit: int = 30, activity_type: str | None = None
) -> list[dict[str, Any]]:
    """List recent activities (newest first) with key stats: duration (s), distance (m),
    heart rate, training effect and load. activity_type filters by Garmin type key,
    e.g. running, cycling, strength_training, golf."""
    end = date.today()
    start = end - timedelta(days=max(1, min(days, 365)))
    params: dict[str, Any] = {
        "start": 0,
        "limit": max(1, min(limit, 100)),
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    }
    if activity_type:
        params["activityType"] = activity_type
    data = await garmin().get("/activitylist-service/activities/search/activities", params)
    return [_slim_activity(a) for a in (data or [])]


@mcp.tool(annotations=READ_ONLY)
async def get_activity(activity_id: int) -> Any:
    """Full details for one activity (summary, laps/splits info, HR, power, training effect)."""
    return await garmin().get(f"/activity-service/activity/{int(activity_id)}")


@mcp.tool(annotations=READ_ONLY)
async def get_activity_workout(activity_id: int) -> Any:
    """Sets, reps and weights for a strength activity, or the structured workout it followed."""
    return await garmin().get(f"/activity-service/activity/{int(activity_id)}/workouts")


@mcp.tool(annotations=READ_ONLY)
async def get_daily_heart_rate(day: str = "today") -> dict[str, Any]:
    """Resting, min and max heart rate for a day (YYYY-MM-DD, 'today' or 'yesterday').
    The minute-by-minute samples are dropped to keep the answer small."""
    data = await garmin().get("/wellness-service/wellness/dailyHeartRate", {"date": _day(day)}) or {}
    data.pop("heartRateValues", None)
    data.pop("heartRateValueDescriptors", None)
    return data


@mcp.tool(annotations=READ_ONLY)
async def get_sleep(day: str = "today") -> dict[str, Any]:
    """Sleep for the night ending on this day: duration, stages, sleep score, overnight HRV
    and resting HR. Per-minute series are dropped to keep the answer small."""
    data = await garmin().get("/sleep-service/sleep/dailySleep", {"date": _day(day)}) or {}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if not (isinstance(v, list) and len(v) > 20)}
    return data


@mcp.tool(annotations=READ_ONLY)
async def get_sleep_summaries(start_date: str, end_date: str) -> Any:
    """Nightly sleep summaries for a date range (YYYY-MM-DD), handy for trends."""
    return await garmin().get(
        "/sleep-service/sleep/dailySleepSummariesByDate",
        {"startDate": _day(start_date), "endDate": _day(end_date)},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_daily_summaries(start_date: str, end_date: str) -> Any:
    """Daily totals for a date range: steps, calories, intensity minutes, resting HR,
    stress and Body Battery."""
    return await garmin().get(
        "/usersummary-service/v2/dailysummaries",
        {"startDate": _day(start_date), "endDate": _day(end_date)},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_hrv(start_date: str, end_date: str) -> Any:
    """Overnight heart-rate variability summaries for a date range (recovery signal)."""
    return await garmin().get(f"/hrv-service/hrv/daily/{_day(start_date)}/{_day(end_date)}")


@mcp.tool(annotations=READ_ONLY)
async def get_vo2_max(start_date: str, end_date: str) -> Any:
    """Daily VO2 max estimates for a date range (running and cycling)."""
    return await garmin().get(f"/metrics-service/metrics/maxmet/daily/{_day(start_date)}/{_day(end_date)}")


def build_app():
    secret = os.environ.get("CONNECTOR_SECRET", "").strip()
    if len(secret) < 24:
        raise SystemExit("Set CONNECTOR_SECRET to a random string of at least 24 characters.")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = mcp.streamable_http_app(
        streamable_http_path=f"/{secret}/mcp",
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
    )
    endpoint = f"/{secret}/mcp"

    async def case_tolerant(scope, receive, send):
        # Accept /<secret>/MCP (or /mcp/) as well as /<secret>/mcp. The secret stays case-sensitive.
        if scope["type"] == "http":
            path = scope["path"].rstrip("/")
            if path != endpoint and path.startswith(f"/{secret}/") and path.lower().endswith("/mcp") \
                    and len(path) == len(endpoint):
                scope = {**scope, "path": endpoint, "raw_path": endpoint.encode()}
        await app(scope, receive, send)

    return case_tolerant


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")
