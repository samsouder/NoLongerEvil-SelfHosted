"""Tests for the development-only UI preview server."""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def dev_ui_module():
    """Load scripts/dev_ui.py without making scripts a package."""
    module_path = Path(__file__).parents[1] / "scripts" / "dev_ui.py"
    spec = importlib.util.spec_from_file_location("nolongerevil_dev_ui", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def dev_ui_client(aiohttp_client, dev_ui_module):
    """Create a test client for the development preview app."""
    return await aiohttp_client(dev_ui_module.create_app())


@pytest.mark.asyncio
async def test_dev_ui_serves_webui_and_mock_devices(dev_ui_client):
    """The dev command serves the real UI backed by fake thermostat data."""
    resp = await dev_ui_client.get("/")
    assert resp.status == 200
    assert "No Longer Evil" in await resp.text()

    resp = await dev_ui_client.get("/api/devices")
    assert resp.status == 200
    payload = await resp.json()
    devices = payload["devices"]
    assert payload["total"] == 5
    assert {device["serial"] for device in devices} == {
        "DEV-HEAT-001",
        "DEV-AUX-002",
        "DEV-AC-003",
        "DEV-FAN-004",
        "DEV-RANGE-005",
    }
    assert any(device["hvac"].get("aux_heat") for device in devices)
    assert any(device["fan_timer_active"] for device in devices)


@pytest.mark.asyncio
async def test_dev_ui_commands_mutate_in_memory_device_state(dev_ui_client):
    """Controls in the UI can change the fake devices through /command."""
    resp = await dev_ui_client.post(
        "/command",
        json={"serial": "DEV-HEAT-001", "command": "set_mode", "value": "cool"},
    )
    assert resp.status == 200
    assert (await resp.json())["success"] is True

    resp = await dev_ui_client.get("/api/devices")
    payload = await resp.json()
    heat_device = next(device for device in payload["devices"] if device["serial"] == "DEV-HEAT-001")
    assert heat_device["mode"] == "cool"

    resp = await dev_ui_client.get("/api/schedule?serial=DEV-HEAT-001")
    schedule = (await resp.json())["schedule"]
    assert schedule["schedule_mode"] == "COOL"


@pytest.mark.asyncio
async def test_dev_ui_provides_usage_history_and_dashboard_data(dev_ui_client):
    """Usage widgets and the expanded dashboard have enough data to render."""
    resp = await dev_ui_client.get("/api/usage-history?serial=DEV-AUX-002&days=3")
    assert resp.status == 200
    compact = await resp.json()
    assert len(compact["days"]) == 3
    assert compact["timeline"]["segments"]

    resp = await dev_ui_client.get("/api/usage-dashboard?serial=DEV-AUX-002&range=week")
    assert resp.status == 200
    dashboard = await resp.json()
    assert dashboard["totals"]["active_seconds"] > 0
    assert set(dashboard["states"]) == {"heat", "aux_heat", "ac", "fan"}
    assert dashboard["trend"]
    assert dashboard["calendar"]
    assert dashboard["peak_days"]
    assert dashboard["context"]["available"] is True

    selected_date = dashboard["range"]["end"]
    resp = await dev_ui_client.get(
        f"/api/usage-dashboard/timeline?serial=DEV-AUX-002&date={selected_date}"
    )
    assert resp.status == 200
    timeline = await resp.json()
    assert timeline["segments"]
    assert timeline["snapshots"]
