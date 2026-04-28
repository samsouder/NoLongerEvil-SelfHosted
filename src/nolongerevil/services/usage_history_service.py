"""HVAC runtime history collection and aggregation."""

import asyncio
import contextlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import (
    DeviceStateChange,
    HvacUsageSegment,
    HvacUsageState,
    ThermostatStateSnapshot,
)
from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager
from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)

DEFAULT_HISTORY_DAYS = 10
MAX_HISTORY_DAYS = 90
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
TRACKED_OBJECT_PREFIXES = ("device.", "shared.")
USAGE_DASHBOARD_STATES = (
    HvacUsageState.HEAT,
    HvacUsageState.AUX_HEAT,
    HvacUsageState.AC,
    HvacUsageState.FAN,
)
SHORT_CYCLE_SECONDS = 5 * 60
SNAPSHOT_RELEVANT_FIELDS = {
    "away",
    "current_humidity",
    "current_temperature",
    "eco",
    "hvac_ac_state",
    "hvac_alt_heat_state",
    "hvac_aux_heater_state",
    "hvac_cool_x2_state",
    "hvac_cool_x3_state",
    "hvac_fan_state",
    "hvac_heat_x2_state",
    "hvac_heat_x3_state",
    "hvac_heater_state",
    "is_online",
    "target_temperature",
    "target_temperature_high",
    "target_temperature_low",
    "target_temperature_type",
}


class UsageHistoryService:
    """Collect and serve HVAC usage history."""

    def __init__(
        self,
        storage: AbstractDeviceStateManager,
        state_service: DeviceStateService,
        retention_days: int | None = None,
    ) -> None:
        self._storage = storage
        self._state_service = state_service
        self._retention_days = retention_days if retention_days and retention_days > 0 else None
        self._active_segments: dict[str, dict[HvacUsageState, int]] = {}
        self._lock = asyncio.Lock()
        self._prune_task: asyncio.Task[None] | None = None
        self._running = False

    async def initialize(self) -> None:
        """Initialize usage history collection and recover active states."""
        async with self._lock:
            if self._running:
                return

            self._running = True
            closed = await self._storage.close_stale_hvac_usage_segments()
            if closed:
                logger.info(f"Closed {closed} stale HVAC usage segment(s)")

            if self._retention_days is not None:
                pruned = await self._prune_old_segments()
                if pruned:
                    logger.info(f"Pruned {pruned} expired HVAC usage segment(s)")

            startup_time = datetime.now()
            for serial in self._state_service.get_all_serials():
                await self._sync_serial(serial, startup_time)

        if self._retention_days is not None:
            self._prune_task = asyncio.create_task(self._prune_loop())

    async def close(self) -> None:
        """Close all active segments and stop background work."""
        async with self._lock:
            now = datetime.now()
            for serial, states in list(self._active_segments.items()):
                for state, segment_id in list(states.items()):
                    await self._storage.update_hvac_usage_segment(
                        segment_id,
                        last_observed_at=now,
                        ended_at=now,
                    )
                    del states[state]
                if not states:
                    self._active_segments.pop(serial, None)
            self._running = False

        if self._prune_task:
            self._prune_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._prune_task
            self._prune_task = None

    async def handle_state_change(self, change: DeviceStateChange) -> None:
        """Track usage whenever device or shared buckets change."""
        if not change.object_key.startswith(TRACKED_OBJECT_PREFIXES):
            return

        observed_at = change.timestamp
        async with self._lock:
            if not self._running:
                return
            await self._record_context_snapshot(change)
            await self._sync_serial(change.serial, observed_at)

    async def get_usage_history(
        self,
        serial: str,
        days: int = DEFAULT_HISTORY_DAYS,
        selected_date: date | None = None,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        """Get summarized usage history and a selected-day timeline."""
        if days < 1:
            days = 1
        if days > MAX_HISTORY_DAYS:
            days = MAX_HISTORY_DAYS

        resolved_timezone_name, timezone = self._resolve_timezone(timezone_name)
        today = self._now(timezone).date()
        timeline_date = selected_date or today
        today_start = self._day_start(today - timedelta(days=days - 1), timezone)
        today_end = self._day_start(today + timedelta(days=1), timezone)
        timeline_start = self._day_start(timeline_date, timezone)
        timeline_end = self._day_start(timeline_date + timedelta(days=1), timezone)

        range_start = min(today_start, timeline_start)
        range_end = max(today_end, timeline_end)
        segments = await self._storage.list_hvac_usage_segments(serial, range_start, range_end)

        return {
            "serial": serial,
            "timezone": resolved_timezone_name,
            "days": self._build_day_summaries(segments, today, days, timezone),
            "timeline": {
                "date": timeline_date.isoformat(),
                "segments": self._build_timeline(segments, timeline_start, timeline_end, timezone),
            },
        }

    async def get_usage_dashboard(
        self,
        serial: str,
        range_type: str = "all",
        start_date: date | None = None,
        end_date: date | None = None,
        timezone_name: str | None = None,
        bucket: str = "auto",
    ) -> dict[str, Any]:
        """Get expanded usage analytics for the linked history dashboard."""
        resolved_timezone_name, timezone = self._resolve_timezone(timezone_name)
        range_start, range_end, normalized_range = await self._resolve_dashboard_range(
            serial,
            range_type,
            start_date,
            end_date,
            timezone,
        )
        bucket = self._normalize_bucket(bucket, range_start, range_end)

        segments = await self._storage.list_hvac_usage_segments(serial, range_start, range_end)
        snapshots = await self._storage.list_thermostat_state_snapshots(
            serial,
            range_start,
            range_end,
        )
        bounds = await self._storage.get_hvac_usage_bounds(serial)

        intervals = self._build_clipped_intervals(segments, range_start, range_end, timezone)
        daily = self._build_dashboard_daily(intervals, range_start.date(), range_end.date())
        state_metrics = self._build_state_metrics(intervals)
        trend = self._build_trend(daily, bucket)
        peak_days = sorted(
            (day for day in daily if day["total_seconds"] > 0),
            key=lambda item: item["total_seconds"],
            reverse=True,
        )[:5]
        active_seconds = self._calculate_active_seconds(intervals)
        fan_overlap_seconds = self._calculate_fan_overlap_seconds(intervals)
        short_cycles = [
            interval
            for interval in intervals
            if interval["state"] != HvacUsageState.FAN.value
            and 0 < interval["duration_seconds"] <= SHORT_CYCLE_SECONDS
        ]

        return {
            "serial": serial,
            "timezone": resolved_timezone_name,
            "range": {
                "type": normalized_range,
                "start": range_start.date().isoformat(),
                "end": (range_end.date() - timedelta(days=1)).isoformat(),
                "bucket": bucket,
                "days": max(0, (range_end.date() - range_start.date()).days),
            },
            "available_range": self._format_available_range(bounds, timezone),
            "totals": self._build_dashboard_totals(
                state_metrics,
                active_seconds,
                fan_overlap_seconds,
                len(short_cycles),
            ),
            "states": state_metrics,
            "trend": trend,
            "calendar": daily,
            "peak_days": peak_days,
            "warnings": self._build_dashboard_warnings(short_cycles),
            "context": self._build_context_summary(snapshots, timezone),
        }

    async def get_usage_dashboard_timeline(
        self,
        serial: str,
        selected_date: date,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        """Get selected-day raw usage segments and context snapshots."""
        resolved_timezone_name, timezone = self._resolve_timezone(timezone_name)
        range_start = self._day_start(selected_date, timezone)
        range_end = self._day_start(selected_date + timedelta(days=1), timezone)
        segments = await self._storage.list_hvac_usage_segments(serial, range_start, range_end)
        snapshots = await self._storage.list_thermostat_state_snapshots(
            serial,
            range_start,
            range_end,
        )
        intervals = self._build_clipped_intervals(segments, range_start, range_end, timezone)
        summary = self._build_dashboard_daily(
            intervals,
            selected_date,
            selected_date + timedelta(days=1),
        )[0]

        return {
            "serial": serial,
            "timezone": resolved_timezone_name,
            "date": selected_date.isoformat(),
            "summary": summary,
            "segments": [
                {
                    "state": interval["state"],
                    "started_at": interval["started_at"].isoformat(),
                    "ended_at": interval["ended_at"].isoformat(),
                    "duration_seconds": interval["duration_seconds"],
                }
                for interval in intervals
            ],
            "snapshots": [
                self._snapshot_to_response(snapshot, timezone)
                for snapshot in snapshots
            ],
        }

    async def _sync_serial(self, serial: str, observed_at: datetime) -> None:
        active_now = self._derive_active_states(serial)
        active_segments = self._active_segments.setdefault(serial, {})

        for state, segment_id in list(active_segments.items()):
            if state not in active_now:
                await self._storage.update_hvac_usage_segment(
                    segment_id,
                    last_observed_at=observed_at,
                    ended_at=observed_at,
                )
                del active_segments[state]

        for state in active_now:
            segment_id = active_segments.get(state)
            if segment_id is None:
                segment = await self._storage.create_hvac_usage_segment(
                    HvacUsageSegment(
                        serial=serial,
                        state=state,
                        started_at=observed_at,
                        last_observed_at=observed_at,
                    )
                )
                if segment.id is None:
                    raise RuntimeError("Created HVAC usage segment is missing an id")
                active_segments[state] = segment.id
            else:
                await self._storage.update_hvac_usage_segment(
                    segment_id,
                    last_observed_at=observed_at,
                )

        if not active_segments:
            self._active_segments.pop(serial, None)

    def _derive_active_states(self, serial: str) -> set[HvacUsageState]:
        shared_obj = self._state_service.get_object(serial, f"shared.{serial}")
        shared_values = shared_obj.value if shared_obj else {}
        active: set[HvacUsageState] = set()

        if (
            shared_values.get("hvac_heater_state")
            or shared_values.get("hvac_heat_x2_state")
            or shared_values.get("hvac_heat_x3_state")
            or shared_values.get("hvac_alt_heat_state")
        ):
            active.add(HvacUsageState.HEAT)

        if (
            shared_values.get("hvac_ac_state")
            or shared_values.get("hvac_cool_x2_state")
            or shared_values.get("hvac_cool_x3_state")
        ):
            active.add(HvacUsageState.AC)

        if shared_values.get("hvac_aux_heater_state"):
            active.add(HvacUsageState.AUX_HEAT)

        if shared_values.get("hvac_fan_state"):
            active.add(HvacUsageState.FAN)

        return active

    async def _record_context_snapshot(self, change: DeviceStateChange) -> None:
        """Persist a thermostat context snapshot when useful fields change."""
        if not set(change.changed_fields).intersection(SNAPSHOT_RELEVANT_FIELDS):
            return

        snapshot = self._build_context_snapshot(change.serial, change.timestamp)
        latest = await self._storage.get_latest_thermostat_state_snapshot(change.serial)
        if latest and self._snapshot_signature(latest) == self._snapshot_signature(snapshot):
            return

        await self._storage.create_thermostat_state_snapshot(snapshot)

    def _build_context_snapshot(
        self,
        serial: str,
        captured_at: datetime,
    ) -> ThermostatStateSnapshot:
        device_obj = self._state_service.get_object(serial, f"device.{serial}")
        shared_obj = self._state_service.get_object(serial, f"shared.{serial}")
        device_values = device_obj.value if device_obj else {}
        shared_values = shared_obj.value if shared_obj else {}
        eco = device_values.get("eco")

        return ThermostatStateSnapshot(
            serial=serial,
            captured_at=captured_at,
            current_temperature=self._number_or_none(
                self._first_present(
                    shared_values.get("current_temperature"),
                    device_values.get("current_temperature"),
                )
            ),
            target_temperature=self._number_or_none(
                self._first_present(
                    shared_values.get("target_temperature"),
                    device_values.get("target_temperature"),
                )
            ),
            target_temperature_high=self._number_or_none(
                self._first_present(
                    shared_values.get("target_temperature_high"),
                    device_values.get("target_temperature_high"),
                )
            ),
            target_temperature_low=self._number_or_none(
                self._first_present(
                    shared_values.get("target_temperature_low"),
                    device_values.get("target_temperature_low"),
                )
            ),
            humidity=self._number_or_none(device_values.get("current_humidity")),
            hvac_mode=shared_values.get("target_temperature_type")
            or device_values.get("target_temperature_type"),
            eco_mode=eco.get("mode") if isinstance(eco, dict) else None,
            away=self._bool_or_none(shared_values.get("away")),
            is_online=self._bool_or_none(device_values.get("is_online")),
        )

    @staticmethod
    def _snapshot_signature(snapshot: ThermostatStateSnapshot) -> tuple[Any, ...]:
        return (
            snapshot.current_temperature,
            snapshot.target_temperature,
            snapshot.target_temperature_high,
            snapshot.target_temperature_low,
            snapshot.humidity,
            snapshot.hvac_mode,
            snapshot.eco_mode,
            snapshot.away,
            snapshot.is_online,
        )

    async def _resolve_dashboard_range(
        self,
        serial: str,
        range_type: str,
        start_date: date | None,
        end_date: date | None,
        timezone: Any,
    ) -> tuple[datetime, datetime, str]:
        range_type = range_type.lower()
        today = self._now(timezone).date()

        if range_type == "custom":
            if start_date is None or end_date is None:
                raise ValueError("custom range requires start and end dates")
            if end_date < start_date:
                raise ValueError("end date must be on or after start date")
            return (
                self._day_start(start_date, timezone),
                self._day_start(end_date + timedelta(days=1), timezone),
                "custom",
            )

        if range_type == "year":
            anchor = start_date or today
            year_start = date(anchor.year, 1, 1)
            return (
                self._day_start(year_start, timezone),
                self._day_start(date(anchor.year + 1, 1, 1), timezone),
                "year",
            )

        if range_type == "month":
            anchor = start_date or today
            month_start = date(anchor.year, anchor.month, 1)
            return (
                self._day_start(month_start, timezone),
                self._day_start(self._next_month(month_start), timezone),
                "month",
            )

        if range_type != "all":
            raise ValueError("range must be all, year, month, or custom")

        bounds = await self._storage.get_hvac_usage_bounds(serial)
        if bounds is None:
            return (
                self._day_start(today, timezone),
                self._day_start(today + timedelta(days=1), timezone),
                "all",
            )

        start_at = self._as_usage_timezone(bounds[0], timezone).date()
        end_at = max(self._as_usage_timezone(bounds[1], timezone).date(), today)
        return (
            self._day_start(start_at, timezone),
            self._day_start(end_at + timedelta(days=1), timezone),
            "all",
        )

    @staticmethod
    def _normalize_bucket(bucket: str, range_start: datetime, range_end: datetime) -> str:
        bucket = bucket.lower()
        if bucket in {"daily", "monthly"}:
            return bucket
        if bucket != "auto":
            raise ValueError("bucket must be auto, daily, or monthly")
        days = max(1, (range_end.date() - range_start.date()).days)
        return "monthly" if days > 120 else "daily"

    def _build_clipped_intervals(
        self,
        segments: list[HvacUsageSegment],
        range_start: datetime,
        range_end: datetime,
        timezone: Any,
    ) -> list[dict[str, Any]]:
        now = self._now(timezone)
        intervals: list[dict[str, Any]] = []
        for segment in segments:
            segment_start = self._as_usage_timezone(segment.started_at, timezone)
            segment_end = self._as_usage_timezone(segment.ended_at or now, timezone)
            start_at = max(segment_start, range_start)
            end_at = min(segment_end, range_end)
            duration_seconds = max(0, int((end_at - start_at).total_seconds()))
            if duration_seconds <= 0:
                continue
            intervals.append(
                {
                    "state": segment.state.value,
                    "started_at": start_at,
                    "ended_at": end_at,
                    "duration_seconds": duration_seconds,
                }
            )
        intervals.sort(key=lambda item: (item["started_at"], item["state"]))
        return intervals

    def _build_dashboard_daily(
        self,
        intervals: list[dict[str, Any]],
        start_day: date,
        end_day: date,
    ) -> list[dict[str, Any]]:
        daily: dict[str, dict[str, Any]] = {}
        active_ranges_by_day: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        cursor = start_day
        while cursor < end_day:
            daily[cursor.isoformat()] = self._empty_dashboard_day(cursor)
            cursor += timedelta(days=1)

        for interval in intervals:
            cursor_dt = interval["started_at"]
            while cursor_dt < interval["ended_at"]:
                day_start = self._day_start(cursor_dt.date(), cursor_dt.tzinfo)
                day_end = day_start + timedelta(days=1)
                overlap_end = min(interval["ended_at"], day_end)
                seconds = max(0, int((overlap_end - cursor_dt).total_seconds()))
                key = cursor_dt.date().isoformat()
                if seconds > 0 and key in daily:
                    field = f"{interval['state']}_seconds"
                    daily[key][field] += seconds
                    active_ranges_by_day[key].append((cursor_dt, overlap_end))
                cursor_dt = overlap_end

        for key, active_ranges in active_ranges_by_day.items():
            daily[key]["total_seconds"] = self._merged_range_seconds(active_ranges)

        return [daily[key] for key in sorted(daily)]

    def _build_state_metrics(self, intervals: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        metrics = {
            state.value: {
                "seconds": 0,
                "run_count": 0,
                "average_run_seconds": 0,
                "longest_run_seconds": 0,
            }
            for state in USAGE_DASHBOARD_STATES
        }

        for interval in intervals:
            state_metrics = metrics[interval["state"]]
            duration = interval["duration_seconds"]
            state_metrics["seconds"] += duration
            state_metrics["run_count"] += 1
            state_metrics["longest_run_seconds"] = max(
                state_metrics["longest_run_seconds"],
                duration,
            )

        for state_metrics in metrics.values():
            run_count = state_metrics["run_count"]
            if run_count:
                state_metrics["average_run_seconds"] = int(state_metrics["seconds"] / run_count)

        return metrics

    def _build_trend(self, daily: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
        if bucket == "daily":
            return [
                {
                    "bucket_start": day["date"],
                    "bucket_end": day["date"],
                    "heat_seconds": day["heat_seconds"],
                    "aux_heat_seconds": day["aux_heat_seconds"],
                    "ac_seconds": day["ac_seconds"],
                    "fan_seconds": day["fan_seconds"],
                    "total_seconds": day["total_seconds"],
                }
                for day in daily
            ]

        monthly: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "bucket_start": "",
                "bucket_end": "",
                "heat_seconds": 0,
                "aux_heat_seconds": 0,
                "ac_seconds": 0,
                "fan_seconds": 0,
                "total_seconds": 0,
            }
        )
        for day in daily:
            day_date = date.fromisoformat(day["date"])
            month_start = date(day_date.year, day_date.month, 1)
            month_key = month_start.isoformat()
            bucket_row = monthly[month_key]
            bucket_row["bucket_start"] = month_key
            bucket_row["bucket_end"] = (self._next_month(month_start) - timedelta(days=1)).isoformat()
            for field in (
                "heat_seconds",
                "aux_heat_seconds",
                "ac_seconds",
                "fan_seconds",
                "total_seconds",
            ):
                bucket_row[field] += day[field]

        return [monthly[key] for key in sorted(monthly)]

    def _build_dashboard_totals(
        self,
        state_metrics: dict[str, dict[str, int]],
        active_seconds: int,
        fan_overlap_seconds: int,
        short_cycle_count: int,
    ) -> dict[str, Any]:
        heat_seconds = state_metrics[HvacUsageState.HEAT.value]["seconds"]
        aux_seconds = state_metrics[HvacUsageState.AUX_HEAT.value]["seconds"]
        ac_seconds = state_metrics[HvacUsageState.AC.value]["seconds"]
        fan_seconds = state_metrics[HvacUsageState.FAN.value]["seconds"]
        conditioning_seconds = heat_seconds + aux_seconds + ac_seconds
        heat_cool_seconds = heat_seconds + aux_seconds + ac_seconds

        return {
            "heat_seconds": heat_seconds,
            "aux_heat_seconds": aux_seconds,
            "ac_seconds": ac_seconds,
            "fan_seconds": fan_seconds,
            "active_seconds": active_seconds,
            "total_seconds": active_seconds,
            "conditioning_seconds": conditioning_seconds,
            "lane_seconds": heat_cool_seconds + fan_seconds,
            "run_count": sum(state["run_count"] for state in state_metrics.values()),
            "longest_run_seconds": max(
                (state["longest_run_seconds"] for state in state_metrics.values()),
                default=0,
            ),
            "heat_cool_balance": {
                "heat_seconds": heat_seconds + aux_seconds,
                "ac_seconds": ac_seconds,
            },
            "aux_heat_ratio": (aux_seconds / (heat_seconds + aux_seconds))
            if (heat_seconds + aux_seconds)
            else 0,
            "fan_overlap_seconds": fan_overlap_seconds,
            "fan_only_seconds": max(0, fan_seconds - fan_overlap_seconds),
            "short_cycle_count": short_cycle_count,
        }

    def _calculate_active_seconds(self, intervals: list[dict[str, Any]]) -> int:
        return self._merged_range_seconds(
            [(interval["started_at"], interval["ended_at"]) for interval in intervals]
        )

    def _calculate_fan_overlap_seconds(self, intervals: list[dict[str, Any]]) -> int:
        fan_intervals = [
            interval for interval in intervals if interval["state"] == HvacUsageState.FAN.value
        ]
        conditioning_intervals = [
            interval
            for interval in intervals
            if interval["state"]
            in {
                HvacUsageState.HEAT.value,
                HvacUsageState.AUX_HEAT.value,
                HvacUsageState.AC.value,
            }
        ]
        overlap_seconds = 0
        for fan in fan_intervals:
            overlap_seconds += self._interval_overlap_seconds(fan, conditioning_intervals)
        return overlap_seconds

    @staticmethod
    def _interval_overlap_seconds(
        interval: dict[str, Any],
        other_intervals: list[dict[str, Any]],
    ) -> int:
        overlaps = []
        for other in other_intervals:
            start_at = max(interval["started_at"], other["started_at"])
            end_at = min(interval["ended_at"], other["ended_at"])
            if end_at > start_at:
                overlaps.append((start_at, end_at))
        if not overlaps:
            return 0

        overlaps.sort()
        merged = [overlaps[0]]
        for start_at, end_at in overlaps[1:]:
            previous_start, previous_end = merged[-1]
            if start_at <= previous_end:
                merged[-1] = (previous_start, max(previous_end, end_at))
            else:
                merged.append((start_at, end_at))

        return sum(int((end_at - start_at).total_seconds()) for start_at, end_at in merged)

    @staticmethod
    def _merged_range_seconds(ranges: list[tuple[datetime, datetime]]) -> int:
        if not ranges:
            return 0

        ordered = sorted(ranges)
        merged = [ordered[0]]
        for start_at, end_at in ordered[1:]:
            previous_start, previous_end = merged[-1]
            if start_at <= previous_end:
                merged[-1] = (previous_start, max(previous_end, end_at))
            else:
                merged.append((start_at, end_at))

        return sum(int((end_at - start_at).total_seconds()) for start_at, end_at in merged)

    @staticmethod
    def _build_dashboard_warnings(short_cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(short_cycles) < 3:
            return []
        by_state: dict[str, int] = defaultdict(int)
        for interval in short_cycles:
            by_state[interval["state"]] += 1
        return [
            {
                "type": "short_cycle",
                "state": state,
                "count": count,
                "threshold_seconds": SHORT_CYCLE_SECONDS,
            }
            for state, count in sorted(by_state.items())
            if count
        ]

    def _build_context_summary(
        self,
        snapshots: list[ThermostatStateSnapshot],
        timezone: Any,
    ) -> dict[str, Any]:
        latest = snapshots[-1] if snapshots else None
        return {
            "available": bool(snapshots),
            "snapshot_count": len(snapshots),
            "latest": self._snapshot_to_response(latest, timezone) if latest else None,
        }

    def _format_available_range(
        self,
        bounds: tuple[datetime, datetime] | None,
        timezone: Any,
    ) -> dict[str, str] | None:
        if bounds is None:
            return None
        return {
            "start": self._as_usage_timezone(bounds[0], timezone).date().isoformat(),
            "end": self._as_usage_timezone(bounds[1], timezone).date().isoformat(),
        }

    def _snapshot_to_response(
        self,
        snapshot: ThermostatStateSnapshot | None,
        timezone: Any,
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            "captured_at": self._as_usage_timezone(snapshot.captured_at, timezone).isoformat(),
            "current_temperature": snapshot.current_temperature,
            "target_temperature": snapshot.target_temperature,
            "target_temperature_high": snapshot.target_temperature_high,
            "target_temperature_low": snapshot.target_temperature_low,
            "humidity": snapshot.humidity,
            "hvac_mode": snapshot.hvac_mode,
            "eco_mode": snapshot.eco_mode,
            "away": snapshot.away,
            "is_online": snapshot.is_online,
        }

    @staticmethod
    def _empty_dashboard_day(day: date) -> dict[str, Any]:
        return {
            "date": day.isoformat(),
            "heat_seconds": 0,
            "aux_heat_seconds": 0,
            "ac_seconds": 0,
            "fan_seconds": 0,
            "total_seconds": 0,
        }

    @staticmethod
    def _number_or_none(value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            return float(value)
        return None

    @staticmethod
    def _first_present(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _bool_or_none(value: Any) -> bool | None:
        if value is None:
            return None
        return bool(value)

    @staticmethod
    def _next_month(day: date) -> date:
        if day.month == 12:
            return date(day.year + 1, 1, 1)
        return date(day.year, day.month + 1, 1)

    async def _prune_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
                async with self._lock:
                    if not self._running:
                        return
                    pruned = await self._prune_old_segments()
                    if pruned:
                        logger.info(f"Pruned {pruned} expired HVAC usage segment(s)")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error(f"Failed to prune HVAC usage history: {exc}")

    async def _prune_old_segments(self) -> int:
        if self._retention_days is None:
            return 0

        cutoff = datetime.now() - timedelta(days=self._retention_days)
        return await self._storage.prune_hvac_usage_segments(cutoff)

    def _build_day_summaries(
        self,
        segments: list[HvacUsageSegment],
        today: date,
        days: int,
        timezone: Any,
    ) -> list[dict[str, Any]]:
        summary_by_date: dict[str, dict[str, Any]] = {}
        ordered_dates: list[date] = []

        for offset in range(days):
            day = today - timedelta(days=offset)
            key = day.isoformat()
            ordered_dates.append(day)
            summary_by_date[key] = {
                "date": key,
                "heat_seconds": 0,
                "ac_seconds": 0,
                "aux_heat_seconds": 0,
                "fan_seconds": 0,
            }

        now = self._now(timezone)
        for segment in segments:
            start_at = self._as_usage_timezone(segment.started_at, timezone)
            effective_end = self._as_usage_timezone(segment.ended_at or now, timezone)
            cursor = start_at
            while cursor < effective_end:
                day_start = self._day_start(cursor.date(), timezone)
                day_end = day_start + timedelta(days=1)
                overlap_start = max(start_at, day_start)
                overlap_end = min(effective_end, day_end)
                seconds = max(0, int((overlap_end - overlap_start).total_seconds()))
                key = day_start.date().isoformat()
                if seconds > 0 and key in summary_by_date:
                    summary_by_date[key][f"{segment.state.value}_seconds"] += seconds
                cursor = day_end

        return [summary_by_date[day.isoformat()] for day in ordered_dates]

    def _build_timeline(
        self,
        segments: list[HvacUsageSegment],
        timeline_start: datetime,
        timeline_end: datetime,
        timezone: Any,
    ) -> list[dict[str, Any]]:
        now = self._now(timezone)
        timeline = []
        for segment in segments:
            effective_end = self._as_usage_timezone(segment.ended_at or now, timezone)
            start_at = max(self._as_usage_timezone(segment.started_at, timezone), timeline_start)
            end_at = min(effective_end, timeline_end)
            duration_seconds = max(0, int((end_at - start_at).total_seconds()))
            if duration_seconds <= 0:
                continue
            timeline.append(
                {
                    "state": segment.state.value,
                    "started_at": start_at.isoformat(),
                    "ended_at": end_at.isoformat(),
                    "duration_seconds": duration_seconds,
                }
            )
        timeline.sort(key=lambda item: (item["started_at"], item["state"]))
        return timeline

    @staticmethod
    def _day_start(day: date, timezone: Any) -> datetime:
        return datetime.combine(day, dt_time.min, tzinfo=timezone)

    @staticmethod
    def _now(timezone: Any) -> datetime:
        return datetime.now(timezone)

    @staticmethod
    def _as_usage_timezone(timestamp: datetime, timezone: Any) -> datetime:
        return datetime.fromtimestamp(timestamp.timestamp(), timezone)

    @staticmethod
    def _resolve_timezone(timezone_name: str | None) -> tuple[str, Any]:
        if timezone_name:
            try:
                timezone = ZoneInfo(timezone_name)
                return timezone_name, timezone
            except ZoneInfoNotFoundError:
                logger.warning(
                    f"Unknown usage history timezone '{timezone_name}', falling back to UTC"
                )

        fallback = ZoneInfo("UTC")
        return "UTC", fallback
