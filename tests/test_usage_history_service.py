"""Tests for HVAC usage history collection service."""

from datetime import datetime, timedelta

import pytest

from nolongerevil.lib.types import DeviceObject, HvacUsageSegment, HvacUsageState
from nolongerevil.services.usage_history_service import UsageHistoryService


class _UsageCallbackManager:
    """Minimal callback manager for state service tests."""

    def __init__(self, usage_history_service: UsageHistoryService) -> None:
        self._usage_history_service = usage_history_service

    async def on_device_state_change(self, change) -> None:
        await self._usage_history_service.handle_state_change(change)


@pytest.mark.asyncio
async def test_usage_history_tracks_heat_ac_aux_and_fan(
    sqlmodel_service,
    state_service,
):
    """Track transitions across heat, AC, aux heat, and fan lanes."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    state_service.set_integration_manager(_UsageCallbackManager(usage_history))
    await usage_history.initialize()

    serial = "TRACK123"
    base = datetime.now().replace(microsecond=1000)

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=1,
            object_timestamp=1,
            value={"hvac_heater_state": True, "hvac_fan_state": True},
            updated_at=base,
        )
    )

    open_segments = await sqlmodel_service.get_open_hvac_usage_segments()
    assert {segment.state for segment in open_segments} == {
        HvacUsageState.HEAT,
        HvacUsageState.FAN,
    }

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=2,
            object_timestamp=2,
            value={"hvac_heater_state": False, "hvac_ac_state": True, "hvac_fan_state": True},
            updated_at=base + timedelta(minutes=10),
        )
    )

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=3,
            object_timestamp=3,
            value={"hvac_ac_state": False, "hvac_aux_heater_state": True, "hvac_fan_state": False},
            updated_at=base + timedelta(minutes=20),
        )
    )

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=4,
            object_timestamp=4,
            value={"hvac_aux_heater_state": False},
            updated_at=base + timedelta(minutes=30),
        )
    )

    segments = await sqlmodel_service.list_hvac_usage_segments(
        serial,
        base - timedelta(minutes=1),
        base + timedelta(minutes=31),
    )
    assert {segment.state for segment in segments} == {
        HvacUsageState.FAN,
        HvacUsageState.HEAT,
        HvacUsageState.AC,
        HvacUsageState.AUX_HEAT,
    }
    assert all(segment.ended_at is not None for segment in segments)

    heat_segment = next(segment for segment in segments if segment.state == HvacUsageState.HEAT)
    assert heat_segment.ended_at == base + timedelta(minutes=10)

    fan_segment = next(segment for segment in segments if segment.state == HvacUsageState.FAN)
    assert fan_segment.ended_at == base + timedelta(minutes=20)

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_ignores_unrelated_updates(
    sqlmodel_service,
    state_service,
):
    """Ignore device updates that do not imply active HVAC runtime."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    state_service.set_integration_manager(_UsageCallbackManager(usage_history))
    await usage_history.initialize()

    serial = "NOHVAC1"
    now = datetime.now().replace(microsecond=1000)

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=1,
            object_timestamp=1,
            value={"target_temperature": 21.0},
            updated_at=now,
        )
    )

    open_segments = await sqlmodel_service.get_open_hvac_usage_segments()
    assert open_segments == []

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_records_context_snapshots_for_relevant_changes(
    sqlmodel_service,
    state_service,
):
    """Capture context snapshots only when dashboard-relevant state changes."""
    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    state_service.set_integration_manager(_UsageCallbackManager(usage_history))
    await usage_history.initialize()

    serial = "SNAP123"
    now = datetime.now().replace(microsecond=1000)

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=1,
            object_timestamp=1,
            value={"where_id": "kitchen"},
            updated_at=now,
        )
    )
    snapshots = await sqlmodel_service.list_thermostat_state_snapshots(
        serial,
        now - timedelta(minutes=1),
        now + timedelta(minutes=1),
    )
    assert snapshots == []

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=2,
            object_timestamp=2,
            value={
                "current_temperature": 20.5,
                "target_temperature": 21,
                "target_temperature_type": "heat",
                "away": False,
            },
            updated_at=now + timedelta(seconds=10),
        )
    )
    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"device.{serial}",
            object_revision=3,
            object_timestamp=3,
            value={
                "current_humidity": 42,
                "eco": {"mode": "schedule"},
                "is_online": True,
            },
            updated_at=now + timedelta(seconds=20),
        )
    )

    snapshots = await sqlmodel_service.list_thermostat_state_snapshots(
        serial,
        now - timedelta(minutes=1),
        now + timedelta(minutes=1),
    )
    assert len(snapshots) == 2
    assert snapshots[0].current_temperature == 20.5
    assert snapshots[0].target_temperature == 21
    assert snapshots[1].humidity == 42
    assert snapshots[1].eco_mode == "schedule"
    assert snapshots[1].is_online is True

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_startup_recovery_and_shutdown(
    sqlmodel_service,
    state_service,
):
    """Close stale segments on startup, reopen active lanes, and close on shutdown."""
    serial = "RECOVER1"
    base = datetime.now().replace(microsecond=1000)

    await state_service.upsert_object(
        DeviceObject(
            serial=serial,
            object_key=f"shared.{serial}",
            object_revision=1,
            object_timestamp=1,
            value={"hvac_heater_state": True},
            updated_at=base - timedelta(minutes=15),
        )
    )
    stale = await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=base - timedelta(minutes=40),
            last_observed_at=base - timedelta(minutes=20),
        )
    )

    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    before_init = datetime.now()
    await usage_history.initialize()
    after_init = datetime.now()

    segments = await sqlmodel_service.list_hvac_usage_segments(
        serial,
        base - timedelta(hours=1),
        base + timedelta(hours=1),
    )
    assert len(segments) == 2

    stale_segment = next(segment for segment in segments if segment.id == stale.id)
    assert stale_segment.ended_at == stale_segment.last_observed_at

    reopened = next(segment for segment in segments if segment.id != stale.id)
    assert reopened.ended_at is None
    assert before_init <= reopened.started_at <= after_init

    await usage_history.close()
    closed_segments = await sqlmodel_service.list_hvac_usage_segments(
        serial,
        base - timedelta(hours=1),
        datetime.now() + timedelta(hours=1),
    )
    assert all(segment.ended_at is not None for segment in closed_segments)


@pytest.mark.asyncio
async def test_usage_history_default_retains_old_segments(
    sqlmodel_service,
    state_service,
):
    """Keep historical usage indefinitely unless retention is explicitly configured."""
    serial = "KEEPOLD1"
    now = datetime.now().replace(microsecond=1000)
    old_end = now - timedelta(days=120)

    kept = await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.HEAT,
            started_at=old_end - timedelta(minutes=20),
            last_observed_at=old_end - timedelta(minutes=5),
            ended_at=old_end,
        )
    )

    usage_history = UsageHistoryService(sqlmodel_service, state_service)
    await usage_history.initialize()

    segments = await sqlmodel_service.list_hvac_usage_segments(
        serial,
        old_end - timedelta(minutes=30),
        old_end + timedelta(minutes=1),
    )
    assert len(segments) == 1
    assert segments[0].id == kept.id
    assert usage_history._prune_task is None

    await usage_history.close()


@pytest.mark.asyncio
async def test_usage_history_prunes_when_retention_configured(
    sqlmodel_service,
    state_service,
):
    """Allow deployments to opt back into bounded retention."""
    serial = "PRUNEOLD1"
    now = datetime.now().replace(microsecond=1000)
    old_end = now - timedelta(days=120)

    await sqlmodel_service.create_hvac_usage_segment(
        HvacUsageSegment(
            serial=serial,
            state=HvacUsageState.AC,
            started_at=old_end - timedelta(minutes=20),
            last_observed_at=old_end - timedelta(minutes=5),
            ended_at=old_end,
        )
    )

    usage_history = UsageHistoryService(sqlmodel_service, state_service, retention_days=90)
    await usage_history.initialize()

    segments = await sqlmodel_service.list_hvac_usage_segments(
        serial,
        old_end - timedelta(minutes=30),
        old_end + timedelta(minutes=1),
    )
    assert segments == []

    await usage_history.close()
