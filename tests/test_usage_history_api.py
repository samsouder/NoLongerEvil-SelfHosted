"""Tests for the usage history API."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from nolongerevil.lib.types import HvacUsageSegment, HvacUsageState, ThermostatStateSnapshot
from nolongerevil.main import create_control_app
from nolongerevil.services.usage_history_service import UsageHistoryService


@pytest.fixture
async def usage_history_api(
    aiohttp_client,
    sqlmodel_service,
    state_service,
    subscription_manager,
    device_availability,
):
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    await usage_history.initialize()
    app = create_control_app(state_service, subscription_manager, device_availability, sqlmodel_service)
    app["usage_history_service"] = usage_history
    client = await aiohttp_client(app)

    try:
        yield client, usage_history
    finally:
        await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_api_defaults_to_empty_three_day_window(
    usage_history_api,
):
    """Return an empty 3-day window when no history has been collected."""
    client, _usage_history = usage_history_api

    resp = await client.get("/api/usage-history", params={"serial": "EMPTY1"})
    assert resp.status == 200

    payload = await resp.json()
    assert payload["serial"] == "EMPTY1"
    assert payload["timezone"] == "UTC"
    assert len(payload["days"]) == 3
    assert payload["timeline"]["segments"] == []
    assert all(
        day["heat_seconds"] == day["ac_seconds"] == day["aux_heat_seconds"] == day["fan_seconds"] == 0
        for day in payload["days"]
    )


@pytest.mark.asyncio
async def test_usage_history_api_aggregates_days_and_selected_timeline(
    usage_history_api,
    sqlmodel_service,
):
    """Split cross-midnight segments into daily totals and selected-day timeline entries."""
    client, _usage_history = usage_history_api

    serial = "API123"
    utc = ZoneInfo("UTC")
    today = datetime.now(utc).date()
    yesterday = today - timedelta(days=1)
    heat_start = datetime.combine(yesterday, time(hour=23, minute=50), tzinfo=utc)
    heat_end = datetime.combine(today, time(hour=0, minute=10), tzinfo=utc)
    fan_start = datetime.combine(today, time(hour=12, minute=0), tzinfo=utc)
    fan_end = datetime.combine(today, time(hour=12, minute=30), tzinfo=utc)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=heat_start,
            last_observed_at=heat_end,
            ended_at=heat_end,
        )
    )
    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.FAN,
            started_at=fan_start,
            last_observed_at=fan_end,
            ended_at=fan_end,
        )
    )

    resp = await client.get(
        "/api/usage-history",
        params={"serial": serial, "days": 2, "date": today.isoformat(), "tz": "UTC"},
    )
    assert resp.status == 200

    payload = await resp.json()
    assert payload["timezone"] == "UTC"
    assert payload["days"][0]["date"] == today.isoformat()
    assert payload["days"][0]["heat_seconds"] == 600
    assert payload["days"][0]["fan_seconds"] == 1800
    assert payload["days"][1]["date"] == yesterday.isoformat()
    assert payload["days"][1]["heat_seconds"] == 600

    timeline = payload["timeline"]["segments"]
    assert len(timeline) == 2
    assert timeline[0]["state"] == "heat"
    assert timeline[0]["duration_seconds"] == 600
    assert "T00:00:00" in timeline[0]["started_at"]
    assert "T00:10:00" in timeline[0]["ended_at"]
    assert timeline[1]["state"] == "fan"
    assert timeline[1]["duration_seconds"] == 1800


@pytest.mark.asyncio
async def test_usage_history_api_rejects_invalid_days(
    usage_history_api,
):
    """Reject requests outside the allowed day range."""
    client, _usage_history = usage_history_api

    resp = await client.get("/api/usage-history", params={"serial": "BAD1", "days": 91})
    assert resp.status == 400
    payload = await resp.json()
    assert "days must be between 1 and 90" in payload["error"]


@pytest.mark.asyncio
async def test_usage_history_api_applies_requested_timezone_to_day_bucketing(
    usage_history_api,
    sqlmodel_service,
):
    """Convert stored timestamps into the requested usage-history timezone."""
    client, _usage_history = usage_history_api

    serial = "TZ123"
    chicago = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    today = datetime.now(chicago).date()
    yesterday = today - timedelta(days=1)
    start = datetime.combine(yesterday, time(hour=23, minute=30), tzinfo=chicago).astimezone(utc)
    end = datetime.combine(today, time(hour=0, minute=15), tzinfo=chicago).astimezone(utc)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=start,
            last_observed_at=end,
            ended_at=end,
        )
    )

    resp = await client.get(
        "/api/usage-history",
        params={"serial": serial, "days": 2, "date": yesterday.isoformat(), "tz": "America/Chicago"},
    )
    assert resp.status == 200

    payload = await resp.json()
    assert payload["timezone"] == "America/Chicago"
    assert payload["days"][0]["date"] == today.isoformat()
    assert payload["days"][1]["date"] == yesterday.isoformat()
    assert payload["days"][1]["heat_seconds"] == 1800

    timeline = payload["timeline"]["segments"]
    assert len(timeline) == 1
    expected_start = start.astimezone(chicago).replace(microsecond=0).isoformat()
    expected_end = datetime.combine(today, time.min, tzinfo=chicago).isoformat()
    assert timeline[0]["started_at"] == expected_start
    assert timeline[0]["ended_at"] == expected_end


@pytest.mark.asyncio
async def test_usage_history_api_falls_back_to_utc_for_invalid_timezone(
    usage_history_api,
    sqlmodel_service,
):
    """Invalid timezone values should not change bucketing away from UTC."""
    client, _usage_history = usage_history_api

    serial = "BADTZ1"
    utc = ZoneInfo("UTC")
    today = datetime.now(utc).date()
    yesterday = today - timedelta(days=1)
    start = datetime.combine(yesterday, time(hour=23, minute=30), tzinfo=utc)
    end = datetime.combine(today, time(hour=0, minute=15), tzinfo=utc)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=start,
            last_observed_at=end,
            ended_at=end,
        )
    )

    resp = await client.get(
        "/api/usage-history",
        params={"serial": serial, "days": 2, "date": today.isoformat(), "tz": "Not/AZone"},
    )
    assert resp.status == 200

    payload = await resp.json()
    assert payload["timezone"] == "UTC"
    assert payload["days"][0]["date"] == today.isoformat()
    assert payload["days"][0]["heat_seconds"] == 900
    assert payload["days"][1]["date"] == yesterday.isoformat()
    assert payload["days"][1]["heat_seconds"] == 1800


@pytest.mark.asyncio
async def test_usage_dashboard_api_returns_all_time_metrics_and_timeline(
    usage_history_api,
    sqlmodel_service,
):
    """Return expanded all-time usage metrics and selected-day detail."""
    client, _usage_history = usage_history_api

    serial = "DASH123"
    utc = ZoneInfo("UTC")
    day = datetime(2026, 1, 10, tzinfo=utc).date()
    heat_start = datetime.combine(day, time(hour=7), tzinfo=utc)
    heat_end = datetime.combine(day, time(hour=7, minute=20), tzinfo=utc)
    fan_end = datetime.combine(day, time(hour=7, minute=30), tzinfo=utc)
    ac_start = datetime.combine(day, time(hour=15), tzinfo=utc)
    ac_end = datetime.combine(day, time(hour=15, minute=10), tzinfo=utc)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=heat_start,
            last_observed_at=heat_end,
            ended_at=heat_end,
        )
    )
    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.FAN,
            started_at=heat_start,
            last_observed_at=fan_end,
            ended_at=fan_end,
        )
    )
    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.AC,
            started_at=ac_start,
            last_observed_at=ac_end,
            ended_at=ac_end,
        )
    )
    await sqlmodel_service.create_thermostat_state_snapshot(
        ThermostatStateSnapshot(
            serial=serial,
            captured_at=heat_start,
            current_temperature=20.5,
            target_temperature=21.0,
            humidity=44,
            hvac_mode="heat",
            eco_mode="schedule",
            away=False,
            is_online=True,
        )
    )

    resp = await client.get(
        "/api/usage-dashboard",
        params={"serial": serial, "range": "all", "bucket": "daily", "tz": "UTC"},
    )
    assert resp.status == 200
    payload = await resp.json()

    assert payload["serial"] == serial
    assert payload["range"]["type"] == "all"
    assert payload["totals"]["heat_seconds"] == 1200
    assert payload["totals"]["ac_seconds"] == 600
    assert payload["totals"]["fan_seconds"] == 1800
    assert payload["totals"]["active_seconds"] == 2400
    assert payload["totals"]["total_seconds"] == 2400
    assert payload["totals"]["lane_seconds"] == 3600
    assert payload["totals"]["fan_overlap_seconds"] == 1200
    assert payload["totals"]["fan_only_seconds"] == 600
    assert payload["states"]["heat"]["run_count"] == 1
    assert payload["context"]["available"] is True
    assert payload["context"]["latest"]["current_temperature"] == 20.5
    assert payload["peak_days"][0]["date"] == day.isoformat()
    assert payload["peak_days"][0]["total_seconds"] == 2400
    trend_day = next(row for row in payload["trend"] if row["bucket_start"] == day.isoformat())
    assert trend_day["heat_seconds"] == 1200
    assert trend_day["fan_seconds"] == 1800
    assert trend_day["total_seconds"] == 2400

    timeline_resp = await client.get(
        "/api/usage-dashboard/timeline",
        params={"serial": serial, "date": day.isoformat(), "tz": "UTC"},
    )
    assert timeline_resp.status == 200
    timeline = await timeline_resp.json()
    assert timeline["date"] == day.isoformat()
    assert len(timeline["segments"]) == 3
    assert timeline["summary"]["total_seconds"] == 2400
    assert len(timeline["snapshots"]) == 1


@pytest.mark.asyncio
async def test_usage_dashboard_api_supports_week_range(
    usage_history_api,
    sqlmodel_service,
):
    """Return the Monday-through-Sunday week containing the requested anchor date."""
    client, _usage_history = usage_history_api

    serial = "WEEK123"
    utc = ZoneInfo("UTC")
    anchor_day = datetime(2026, 1, 7, tzinfo=utc).date()
    in_week_start = datetime(2026, 1, 6, 8, 0, tzinfo=utc)
    out_of_week_start = datetime(2026, 1, 12, 8, 0, tzinfo=utc)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=in_week_start,
            last_observed_at=in_week_start + timedelta(minutes=10),
            ended_at=in_week_start + timedelta(minutes=10),
        )
    )
    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=out_of_week_start,
            last_observed_at=out_of_week_start + timedelta(minutes=10),
            ended_at=out_of_week_start + timedelta(minutes=10),
        )
    )

    resp = await client.get(
        "/api/usage-dashboard",
        params={"serial": serial, "range": "week", "start": anchor_day.isoformat(), "tz": "UTC"},
    )
    assert resp.status == 200
    payload = await resp.json()

    assert payload["range"]["type"] == "week"
    assert payload["range"]["start"] == "2026-01-05"
    assert payload["range"]["end"] == "2026-01-11"
    assert payload["range"]["days"] == 7
    assert payload["totals"]["active_seconds"] == 600
    assert len(payload["calendar"]) == 7
    assert payload["peak_days"][0]["date"] == "2026-01-06"


@pytest.mark.asyncio
async def test_usage_dashboard_api_validates_range_inputs(
    usage_history_api,
):
    """Reject invalid dashboard range requests with a clear 400."""
    client, _usage_history = usage_history_api

    resp = await client.get(
        "/api/usage-dashboard",
        params={"serial": "BADRANGE", "range": "custom", "start": "2026-01-02"},
    )
    assert resp.status == 400
    payload = await resp.json()
    assert "custom range requires start and end dates" in payload["error"]


@pytest.mark.asyncio
async def test_usage_dashboard_page_loads(
    aiohttp_client,
    sqlmodel_service,
    state_service,
    subscription_manager,
    device_availability,
):
    """Serve the linked usage history dashboard page."""
    app = create_control_app(state_service, subscription_manager, device_availability, sqlmodel_service)
    client = await aiohttp_client(app)

    resp = await client.get("/usage-history")
    assert resp.status == 200
    html = await resp.text()
    assert "Usage History" in html
    assert "/api/usage-dashboard" in html
    assert 'let activeRange = "week";' in html
    assert 'data-range="week"' in html
