#!/usr/bin/env python3
"""Development-only UI preview server with fake thermostats.

This file intentionally lives outside src/nolongerevil so it is not included in
the packaged application wheel. Run it from a checkout with:

    uv run python scripts/dev_ui.py
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import json
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from aiohttp import web

from nolongerevil.routes.control.webui import create_webui_routes

STATE_ORDER = ("heat", "aux_heat", "ac", "fan")
STATE_FIELDS = {
    "heat": "heat_seconds",
    "aux_heat": "aux_heat_seconds",
    "ac": "ac_seconds",
    "fan": "fan_seconds",
}


@dataclass
class DemoThermostat:
    serial: str
    name: str
    mode: str
    current_temperature: float
    target_temperature: float
    target_temperature_low: float
    target_temperature_high: float
    humidity: int
    profile: str
    temperature_scale: str = "F"
    target_humidity: int | None = None
    target_humidity_enabled: bool = False
    eco_mode: str | None = None
    has_leaf: bool = False
    is_available: bool = True
    is_online: bool = True
    fan_timer_timeout: int = 0
    time_to_target: int | None = None
    safety_temp_activating_hvac: bool = False
    capabilities: dict[str, bool] = field(
        default_factory=lambda: {
            "can_heat": True,
            "can_cool": True,
            "has_fan": True,
            "has_emer_heat": True,
            "has_humidifier": True,
            "has_dehumidifier": True,
        }
    )
    hvac: dict[str, bool] = field(default_factory=dict)

    def as_status(self) -> dict[str, Any]:
        now = int(time.time())
        fan_active = self.fan_timer_timeout > now
        return {
            "serial": self.serial,
            "api_key": f"DEV-{self.serial[-4:]}-APIKEY",
            "is_available": self.is_available,
            "last_seen": datetime.now().replace(microsecond=0).isoformat(),
            "name": self.name,
            "current_temperature": self.current_temperature,
            "target_temperature": self.target_temperature,
            "target_temperature_high": self.target_temperature_high,
            "target_temperature_low": self.target_temperature_low,
            "humidity": self.humidity,
            "target_humidity": self.target_humidity,
            "target_humidity_enabled": self.target_humidity_enabled,
            "mode": self.mode,
            "hvac": dict(self.hvac),
            "fan_timer_active": fan_active,
            "fan_timer_timeout": self.fan_timer_timeout,
            "eco_temperatures": {"high": 25.5, "low": 14.5},
            "is_online": self.is_online,
            "has_leaf": self.has_leaf,
            "software_version": "dev-preview",
            "temperature_scale": self.temperature_scale,
            "capabilities": dict(self.capabilities),
            "eco_mode": self.eco_mode,
            "time_to_target": self.time_to_target,
            "time_to_target_training_status": "ready",
            "safety_state": "normal",
            "safety_temp_activating_hvac": self.safety_temp_activating_hvac,
            "learning_mode": "ready",
            "preconditioning_enabled": True,
            "backplate_temperature": self.current_temperature + 0.2,
            "structure_id": "dev-home",
            "away": self.eco_mode == "auto-eco",
            "schedule_mode": schedule_mode_for(self.mode),
            "subscription_count": 1,
        }


def schedule_mode_for(mode: str) -> str:
    if mode == "cool":
        return "COOL"
    if mode == "heat-cool":
        return "RANGE"
    return "HEAT"


def iso_at(day: date, hour: int, minute: int = 0) -> str:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).isoformat()


def default_schedule(mode: str) -> dict[str, Any]:
    schedule_mode = schedule_mode_for(mode)
    days: dict[str, list[dict[str, Any]]] = {}
    for idx in range(7):
        if schedule_mode == "RANGE":
            entries = [
                {
                    "type": "RANGE",
                    "time": 6 * 3600 + 30 * 60,
                    "entry_type": "setpoint",
                    "temp-min": 18.0,
                    "temp-max": 23.0,
                },
                {
                    "type": "RANGE",
                    "time": 22 * 3600,
                    "entry_type": "setpoint",
                    "temp-min": 16.5,
                    "temp-max": 25.0,
                },
            ]
        else:
            day_offset = 0.5 if idx in (5, 6) else 0
            entries = [
                {
                    "type": schedule_mode,
                    "time": 6 * 3600,
                    "entry_type": "setpoint",
                    "temp": 20.5 + day_offset,
                },
                {
                    "type": schedule_mode,
                    "time": 17 * 3600 + 30 * 60,
                    "entry_type": "setpoint",
                    "temp": 21.5 + day_offset,
                },
                {
                    "type": schedule_mode,
                    "time": 22 * 3600,
                    "entry_type": "setpoint",
                    "temp": 18.5,
                },
            ]
        days[str(idx)] = entries
    return {"schedule_mode": schedule_mode, "days": days}


def seed_thermostats() -> dict[str, DemoThermostat]:
    now = int(time.time())
    devices = [
        DemoThermostat(
            serial="DEV-HEAT-001",
            name="Living Room Heat",
            mode="heat",
            current_temperature=19.4,
            target_temperature=21.5,
            target_temperature_low=18.0,
            target_temperature_high=24.0,
            humidity=38,
            profile="heat",
            has_leaf=True,
            time_to_target=now + 18 * 60,
        ),
        DemoThermostat(
            serial="DEV-AUX-002",
            name="Upstairs Aux Heat",
            mode="heat",
            current_temperature=17.8,
            target_temperature=22.0,
            target_temperature_low=17.0,
            target_temperature_high=24.0,
            humidity=34,
            profile="aux",
            time_to_target=now + 42 * 60,
            safety_temp_activating_hvac=True,
        ),
        DemoThermostat(
            serial="DEV-AC-003",
            name="Office AC",
            mode="cool",
            current_temperature=25.7,
            target_temperature=22.0,
            target_temperature_low=18.0,
            target_temperature_high=24.0,
            humidity=58,
            profile="ac",
            time_to_target=now + 24 * 60,
        ),
        DemoThermostat(
            serial="DEV-FAN-004",
            name="Basement Fan",
            mode="off",
            current_temperature=20.2,
            target_temperature=20.5,
            target_temperature_low=17.0,
            target_temperature_high=24.0,
            humidity=51,
            profile="fan",
            fan_timer_timeout=now + 27 * 60,
        ),
        DemoThermostat(
            serial="DEV-RANGE-005",
            name="Guest Room Range",
            mode="heat-cool",
            current_temperature=22.4,
            target_temperature=21.0,
            target_temperature_low=18.5,
            target_temperature_high=24.0,
            humidity=45,
            profile="range",
            target_humidity=40,
            target_humidity_enabled=True,
            hvac={"humidifier": True},
        ),
    ]
    for device in devices:
        apply_runtime_state(device, keep_profile=True)
    return {device.serial: device for device in devices}


def apply_runtime_state(device: DemoThermostat, *, keep_profile: bool = False) -> None:
    humidifier = bool(device.hvac.get("humidifier"))
    device.hvac = {"humidifier": humidifier}

    if device.profile == "aux" and keep_profile:
        device.hvac["aux_heat"] = True
    elif device.profile == "fan" and keep_profile:
        device.hvac["fan"] = True
    elif device.profile == "ac" and keep_profile:
        device.hvac["ac"] = True
    elif device.mode == "heat":
        device.hvac["heater"] = device.current_temperature < device.target_temperature - 0.2
    elif device.mode == "cool":
        device.hvac["ac"] = device.current_temperature > device.target_temperature + 0.2
    elif device.mode == "heat-cool":
        if device.current_temperature < device.target_temperature_low - 0.2:
            device.hvac["heater"] = True
        elif device.current_temperature > device.target_temperature_high + 0.2:
            device.hvac["ac"] = True
    elif device.mode == "emergency":
        device.hvac["emer_heat"] = True

    if device.fan_timer_timeout > int(time.time()):
        device.hvac["fan"] = True


def seed_schedules(devices: dict[str, DemoThermostat]) -> dict[str, dict[str, Any]]:
    return {serial: default_schedule(device.mode) for serial, device in devices.items()}


def day_segments(serial: str, day: date) -> list[dict[str, Any]]:
    rnd = random.Random(f"{serial}:{day.isoformat()}")
    cold_bias = 1 if day.month in {1, 2, 3, 11, 12} else 0
    warm_bias = 1 if day.month in {5, 6, 7, 8, 9} else 0
    segments: list[dict[str, Any]] = []

    heat_minutes = 18 + cold_bias * 18 + rnd.randint(0, 18)
    segments.append(make_segment(day, "heat", 5, 45 + rnd.randint(0, 25), heat_minutes))

    if cold_bias or day.day % 4 == 0 or "AUX" in serial:
        aux_minutes = 9 + rnd.randint(0, 15)
        segments.append(make_segment(day, "aux_heat", 6, 30 + rnd.randint(0, 18), aux_minutes))

    ac_minutes = 24 + warm_bias * 30 + rnd.randint(0, 34)
    segments.append(make_segment(day, "ac", 14 + rnd.randint(0, 3), rnd.randint(0, 50), ac_minutes))

    segments.append(make_segment(day, "fan", 9, rnd.randint(0, 50), 12 + rnd.randint(0, 16)))
    if day.day % 3 == 0 or "FAN" in serial:
        segments.append(make_segment(day, "fan", 19, rnd.randint(0, 40), 15 + rnd.randint(0, 22)))

    return sorted(segments, key=lambda segment: segment["started_at"])


def make_segment(day: date, state: str, hour: int, minute: int, duration_minutes: int) -> dict[str, Any]:
    started = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
    ended = started + timedelta(minutes=duration_minutes)
    return {
        "state": state,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_seconds": int((ended - started).total_seconds()),
    }


def segments_for_range(serial: str, start: date, end: date) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current = start
    while current <= end:
        segments.extend(day_segments(serial, current))
        current += timedelta(days=1)
    return segments


def summarize_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    by_state = {
        state: {"seconds": 0, "run_count": 0, "average_run_seconds": 0} for state in STATE_ORDER
    }
    for segment in segments:
        state = segment["state"]
        if state not in by_state:
            continue
        by_state[state]["seconds"] += segment["duration_seconds"]
        by_state[state]["run_count"] += 1
    for item in by_state.values():
        if item["run_count"]:
            item["average_run_seconds"] = round(item["seconds"] / item["run_count"])

    conditioning = sum(by_state[state]["seconds"] for state in ("heat", "aux_heat", "ac"))
    fan = by_state["fan"]["seconds"]
    total = conditioning + fan
    return {
        "states": by_state,
        "totals": {
            "active_seconds": total,
            "conditioning_seconds": conditioning,
            "fan_only_seconds": fan,
            "fan_overlap_seconds": round(fan * 0.18),
            "run_count": sum(by_state[state]["run_count"] for state in STATE_ORDER),
            "longest_run_seconds": max((s["duration_seconds"] for s in segments), default=0),
        },
    }


def daily_rows(serial: str, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current = start
    while current <= end:
        row = empty_duration_row({"date": current.isoformat()})
        for segment in day_segments(serial, current):
            row[STATE_FIELDS[segment["state"]]] += segment["duration_seconds"]
            row["total_seconds"] += segment["duration_seconds"]
        rows.append(row)
        current += timedelta(days=1)
    return rows


def empty_duration_row(base: dict[str, Any]) -> dict[str, Any]:
    row = dict(base)
    for field_name in STATE_FIELDS.values():
        row[field_name] = 0
    row["total_seconds"] = 0
    return row


def parse_range(query: dict[str, str]) -> tuple[date, date]:
    today = date.today()
    range_type = query.get("range", "week")
    start_raw = query.get("start")
    end_raw = query.get("end")

    if start_raw:
        start = date.fromisoformat(start_raw)
    elif range_type == "year":
        start = date(today.year, 1, 1)
    elif range_type == "month":
        start = today.replace(day=1)
    elif range_type == "week":
        start = today - timedelta(days=6)
    else:
        start = today - timedelta(days=89)

    if end_raw:
        end = date.fromisoformat(end_raw)
    elif range_type == "year":
        end = date(start.year, 12, 31)
    elif range_type == "month":
        end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    elif range_type == "week":
        end = start + timedelta(days=6)
    else:
        end = today

    if end < start:
        start, end = end, start
    return start, end


def build_trend(serial: str, start: date, end: date, bucket: str) -> list[dict[str, Any]]:
    days = daily_rows(serial, start, end)
    span = (end - start).days + 1
    use_months = bucket == "month" or (bucket == "auto" and span > 62)
    if not use_months:
        return [{"bucket_start": row["date"], "bucket_end": row["date"], **row} for row in days]

    grouped: dict[str, dict[str, Any]] = {}
    for row in days:
        month = row["date"][:7]
        month_row = grouped.setdefault(
            month,
            empty_duration_row({"bucket_start": f"{month}-01", "bucket_end": f"{month}-01"}),
        )
        month_row["bucket_end"] = row["date"]
        for field_name in (*STATE_FIELDS.values(), "total_seconds"):
            month_row[field_name] += row[field_name]
    return list(grouped.values())


def build_snapshots(device: DemoThermostat, selected_date: date) -> list[dict[str, Any]]:
    return [
        {
            "captured_at": iso_at(selected_date, hour, 15),
            "current_temperature": device.current_temperature + (idx * 0.3),
            "target_temperature": device.target_temperature,
            "target_temperature_low": device.target_temperature_low,
            "target_temperature_high": device.target_temperature_high,
            "humidity": device.humidity + idx,
            "hvac_mode": device.mode,
            "eco_mode": device.eco_mode,
            "away": device.eco_mode == "auto-eco",
            "is_online": device.is_online,
        }
        for idx, hour in enumerate((6, 12, 18))
    ]


def get_device(request: web.Request, serial: str) -> DemoThermostat | None:
    devices: dict[str, DemoThermostat] = request.app["demo_devices"]
    return devices.get(serial)


async def notify(request: web.Request, serial: str) -> None:
    queues: set[asyncio.Queue[str]] = request.app["event_queues"]
    for queue in list(queues):
        await queue.put(serial)


async def handle_devices(request: web.Request) -> web.Response:
    devices: dict[str, DemoThermostat] = request.app["demo_devices"]
    return web.json_response(
        {"devices": [device.as_status() for device in devices.values()], "total": len(devices)}
    )


async def handle_status(request: web.Request) -> web.Response:
    serial = request.query.get("serial", "")
    device = get_device(request, serial)
    if not device:
        return web.json_response({"error": "Device not found"}, status=404)
    return web.json_response(device.as_status())


async def handle_config(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "api_origin": "http://localhost:8000",
            "cloudregisterurl": "http://localhost:8000/entry",
            "require_device_pairing": False,
            "entry_key_ttl_seconds": 3600,
        }
    )


async def handle_schedule(request: web.Request) -> web.Response:
    serial = request.query.get("serial", "")
    schedules: dict[str, dict[str, Any]] = request.app["demo_schedules"]
    return web.json_response({"serial": serial, "schedule": deepcopy(schedules.get(serial))})


async def handle_command(request: web.Request) -> web.Response:
    body = await request.json()
    serial = body.get("serial")
    command = body.get("command")
    value = body.get("value")
    device = get_device(request, serial)
    if not device:
        return web.json_response({"success": False, "message": "Device not found"}, status=404)

    schedules: dict[str, dict[str, Any]] = request.app["demo_schedules"]
    now = int(time.time())

    if command == "set_temperature":
        if isinstance(value, dict):
            if "low" in value:
                device.target_temperature_low = float(value["low"])
            if "high" in value:
                device.target_temperature_high = float(value["high"])
        else:
            device.target_temperature = float(value)
    elif command == "set_mode":
        device.mode = str(value)
        schedules[serial] = default_schedule(device.mode)
    elif command == "set_away":
        device.eco_mode = "manual-eco" if value else None
    elif command == "set_fan":
        device.fan_timer_timeout = now + 30 * 60 if value == "on" else 0
    elif command == "set_schedule":
        schedules[serial] = value
    else:
        return web.json_response({"success": False, "message": f"Unknown command: {command}"}, status=400)

    apply_runtime_state(device)
    await notify(request, serial)
    return web.json_response({"success": True, "data": {"serial": serial, "command": command}})


async def handle_delete_device(request: web.Request) -> web.Response:
    body = await request.json()
    serial = body.get("serial")
    devices: dict[str, DemoThermostat] = request.app["demo_devices"]
    schedules: dict[str, dict[str, Any]] = request.app["demo_schedules"]
    if serial not in devices:
        return web.json_response({"error": "Device not found"}, status=404)
    del devices[serial]
    schedules.pop(serial, None)
    await notify(request, str(serial))
    return web.json_response({"success": True, "serial": serial, "objects_deleted": 1})


async def handle_usage_history(request: web.Request) -> web.Response:
    serial = request.query.get("serial", "")
    if not get_device(request, serial):
        return web.json_response({"error": "Device not found"}, status=404)

    days = int(request.query.get("days", "3"))
    today = date.today()
    start = today - timedelta(days=days - 1)
    selected = date.fromisoformat(request.query.get("date", today.isoformat()))
    rows = daily_rows(serial, start, today)
    return web.json_response(
        {
            "serial": serial,
            "timezone": request.query.get("tz") or "local",
            "days": rows,
            "timeline": {
                "date": selected.isoformat(),
                "segments": day_segments(serial, selected),
            },
        }
    )


async def handle_usage_dashboard(request: web.Request) -> web.Response:
    serial = request.query.get("serial", "")
    device = get_device(request, serial)
    if not device:
        return web.json_response({"error": "Device not found"}, status=404)

    start, end = parse_range(dict(request.query))
    segments = segments_for_range(serial, start, end)
    summary = summarize_segments(segments)
    calendar_rows = daily_rows(serial, start, end)
    trend = build_trend(serial, start, end, request.query.get("bucket", "auto"))
    peaks = sorted(calendar_rows, key=lambda row: row["total_seconds"], reverse=True)[:5]
    snapshots = build_snapshots(device, end)
    latest = snapshots[-1]

    return web.json_response(
        {
            "serial": serial,
            "timezone": request.query.get("tz") or "local",
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "totals": summary["totals"],
            "states": summary["states"],
            "trend": trend,
            "calendar": calendar_rows,
            "peak_days": peaks,
            "warnings": [{"state": "aux_heat", "count": 4}, {"state": "ac", "count": 2}],
            "context": {
                "available": True,
                "snapshot_count": len(snapshots),
                "latest": latest,
            },
        }
    )


async def handle_usage_dashboard_timeline(request: web.Request) -> web.Response:
    serial = request.query.get("serial", "")
    device = get_device(request, serial)
    if not device:
        return web.json_response({"error": "Device not found"}, status=404)

    selected = date.fromisoformat(request.query.get("date", date.today().isoformat()))
    return web.json_response(
        {
            "serial": serial,
            "date": selected.isoformat(),
            "timezone": request.query.get("tz") or "local",
            "segments": day_segments(serial, selected),
            "snapshots": build_snapshots(device, selected),
        }
    )


async def handle_stats(request: web.Request) -> web.Response:
    devices: dict[str, DemoThermostat] = request.app["demo_devices"]
    return web.json_response(
        {
            "devices": {
                "total": len(devices),
                "available": sum(1 for device in devices.values() if device.is_available),
                "serials": list(devices),
            },
            "subscriptions": {"total": 0},
            "availability": {
                serial: {"is_available": device.is_available, "last_seen": device.as_status()["last_seen"]}
                for serial, device in devices.items()
            },
        }
    )


async def handle_events(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)

    queue: asyncio.Queue[str] = asyncio.Queue()
    request.app["event_queues"].add(queue)
    try:
        await response.write(b'data: {"serial": null, "dev": true}\n\n')
        while True:
            try:
                serial = await asyncio.wait_for(queue.get(), timeout=15)
                payload = json.dumps({"serial": serial})
                await response.write(f"data: {payload}\n\n".encode())
            except TimeoutError:
                await response.write(b": heartbeat\n\n")
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        request.app["event_queues"].discard(queue)
    return response


async def handle_scan_network(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "subnet": "dev-preview/24",
            "devices": [
                {
                    "ip": "192.0.2.10",
                    "device_name": "Preview Nest Hallway",
                    "cloudregisterurl": "https://nest.com/cloud",
                    "configured": False,
                },
                {
                    "ip": "192.0.2.11",
                    "device_name": "Preview Nest Studio",
                    "cloudregisterurl": "http://localhost:8000/entry",
                    "configured": True,
                },
            ],
        }
    )


async def handle_configure_nest(request: web.Request) -> web.Response:
    body = await request.json()
    ip = body.get("ip", "preview")
    return web.json_response({"success": True, "device_name": f"Configured {ip}"})


async def handle_register(_request: web.Request) -> web.Response:
    return web.json_response({"success": True, "message": "Dev preview accepts all entry keys."})


def add_demo_routes(app: web.Application) -> None:
    app.router.add_get("/api/config", handle_config)
    app.router.add_get("/api/devices", handle_devices)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/api/schedule", handle_schedule)
    app.router.add_get("/api/usage-history", handle_usage_history)
    app.router.add_get("/api/usage-dashboard", handle_usage_dashboard)
    app.router.add_get("/api/usage-dashboard/timeline", handle_usage_dashboard_timeline)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_get("/api/events", handle_events)
    app.router.add_post("/command", handle_command)
    app.router.add_delete("/api/device", handle_delete_device)
    app.router.add_post("/api/scan-network", handle_scan_network)
    app.router.add_post("/api/configure-nest", handle_configure_nest)
    app.router.add_post("/api/register", handle_register)


def create_app() -> web.Application:
    devices = seed_thermostats()
    app = web.Application()
    app["demo_devices"] = devices
    app["demo_schedules"] = seed_schedules(devices)
    app["event_queues"] = set()
    add_demo_routes(app)
    create_webui_routes(app)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NoLongerEvil development UI preview.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8083, help="Port to bind. Default: 8083")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(f"Development UI preview: http://{args.host}:{args.port}/")
    print("This server uses in-memory fake thermostats and does not talk to real devices.")
    web.run_app(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
