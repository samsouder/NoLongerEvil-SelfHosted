"""Tests for the usage history API."""

from datetime import date, datetime, time, timedelta

import pytest

from nolongerevil.lib.types import HvacUsageSegment, HvacUsageState
from nolongerevil.main import create_control_app
from nolongerevil.services.usage_history_service import UsageHistoryService


@pytest.mark.asyncio
async def test_usage_history_api_defaults_to_empty_ten_day_window(
    aiohttp_client,
    sqlmodel_service,
    state_service,
    subscription_manager,
    device_availability,
):
    """Return an empty 10-day window when no history has been collected."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    await usage_history.initialize()

    app = create_control_app(state_service, subscription_manager, device_availability, sqlmodel_service)
    app["usage_history_service"] = usage_history
    client = await aiohttp_client(app)

    resp = await client.get("/api/usage-history", params={"serial": "EMPTY1"})
    assert resp.status == 200

    payload = await resp.json()
    assert payload["serial"] == "EMPTY1"
    assert len(payload["days"]) == 10
    assert payload["timeline"]["segments"] == []
    assert all(
        day["heat_seconds"] == day["ac_seconds"] == day["aux_heat_seconds"] == day["fan_seconds"] == 0
        for day in payload["days"]
    )

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_api_aggregates_days_and_selected_timeline(
    aiohttp_client,
    sqlmodel_service,
    state_service,
    subscription_manager,
    device_availability,
):
    """Split cross-midnight segments into daily totals and selected-day timeline entries."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    await usage_history.initialize()

    serial = "API123"
    today = date.today()
    yesterday = today - timedelta(days=1)
    heat_start = datetime.combine(yesterday, time(hour=23, minute=50))
    heat_end = datetime.combine(today, time(hour=0, minute=10))
    fan_start = datetime.combine(today, time(hour=12, minute=0))
    fan_end = datetime.combine(today, time(hour=12, minute=30))

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

    app = create_control_app(state_service, subscription_manager, device_availability, sqlmodel_service)
    app["usage_history_service"] = usage_history
    client = await aiohttp_client(app)

    resp = await client.get(
        "/api/usage-history",
        params={"serial": serial, "days": 2, "date": today.isoformat()},
    )
    assert resp.status == 200

    payload = await resp.json()
    assert payload["days"][0]["date"] == today.isoformat()
    assert payload["days"][0]["heat_seconds"] == 600
    assert payload["days"][0]["fan_seconds"] == 1800
    assert payload["days"][1]["date"] == yesterday.isoformat()
    assert payload["days"][1]["heat_seconds"] == 600

    timeline = payload["timeline"]["segments"]
    assert len(timeline) == 2
    assert timeline[0]["state"] == "heat"
    assert timeline[0]["duration_seconds"] == 600
    assert timeline[0]["started_at"].endswith("00:00:00")
    assert timeline[0]["ended_at"].endswith("00:10:00")
    assert timeline[1]["state"] == "fan"
    assert timeline[1]["duration_seconds"] == 1800

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_api_rejects_invalid_days(
    aiohttp_client,
    sqlmodel_service,
    state_service,
    subscription_manager,
    device_availability,
):
    """Reject requests outside the allowed day range."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    await usage_history.initialize()

    app = create_control_app(state_service, subscription_manager, device_availability, sqlmodel_service)
    app["usage_history_service"] = usage_history
    client = await aiohttp_client(app)

    resp = await client.get("/api/usage-history", params={"serial": "BAD1", "days": 91})
    assert resp.status == 400
    payload = await resp.json()
    assert "days must be between 1 and 90" in payload["error"]

    await usage_history.close()
