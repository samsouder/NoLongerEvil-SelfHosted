"""HVAC runtime history collection and aggregation."""

import asyncio
import contextlib
import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any

from nolongerevil.lib.logger import get_logger
from nolongerevil.lib.types import DeviceStateChange, HvacUsageSegment, HvacUsageState
from nolongerevil.services.abstract_device_state_manager import AbstractDeviceStateManager
from nolongerevil.services.device_state_service import DeviceStateService

logger = get_logger(__name__)

DEFAULT_HISTORY_DAYS = 10
MAX_HISTORY_DAYS = 90
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
TRACKED_OBJECT_PREFIXES = ("device.", "shared.")


class UsageHistoryService:
    """Collect and serve HVAC usage history."""

    def __init__(
        self,
        storage: AbstractDeviceStateManager,
        state_service: DeviceStateService,
        retention_days: int = MAX_HISTORY_DAYS,
    ) -> None:
        self._storage = storage
        self._state_service = state_service
        self._retention_days = retention_days
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

            pruned = await self._prune_old_segments()
            if pruned:
                logger.info(f"Pruned {pruned} expired HVAC usage segment(s)")

            startup_time = datetime.now()
            for serial in self._state_service.get_all_serials():
                await self._sync_serial(serial, startup_time)

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
            await self._sync_serial(change.serial, observed_at)

    async def get_usage_history(
        self,
        serial: str,
        days: int = DEFAULT_HISTORY_DAYS,
        selected_date: date | None = None,
    ) -> dict[str, Any]:
        """Get summarized usage history and a selected-day timeline."""
        if days < 1:
            days = 1
        if days > MAX_HISTORY_DAYS:
            days = MAX_HISTORY_DAYS

        today = datetime.now().date()
        timeline_date = selected_date or today
        today_start = self._day_start(today - timedelta(days=days - 1))
        today_end = self._day_start(today + timedelta(days=1))
        timeline_start = self._day_start(timeline_date)
        timeline_end = self._day_start(timeline_date + timedelta(days=1))

        range_start = min(today_start, timeline_start)
        range_end = max(today_end, timeline_end)
        segments = await self._storage.list_hvac_usage_segments(serial, range_start, range_end)

        return {
            "serial": serial,
            "timezone": self._timezone_name(),
            "days": self._build_day_summaries(segments, today, days),
            "timeline": {
                "date": timeline_date.isoformat(),
                "segments": self._build_timeline(segments, timeline_start, timeline_end),
            },
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
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        return await self._storage.prune_hvac_usage_segments(cutoff)

    def _build_day_summaries(
        self,
        segments: list[HvacUsageSegment],
        today: date,
        days: int,
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

        now = datetime.now()
        for segment in segments:
            effective_end = segment.ended_at or now
            cursor = segment.started_at
            while cursor < effective_end:
                day_start = self._day_start(cursor.date())
                day_end = day_start + timedelta(days=1)
                overlap_start = max(segment.started_at, day_start)
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
    ) -> list[dict[str, Any]]:
        now = datetime.now()
        timeline = []
        for segment in segments:
            effective_end = segment.ended_at or now
            start_at = max(segment.started_at, timeline_start)
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
    def _day_start(day: date) -> datetime:
        return datetime.combine(day, dt_time.min)

    @staticmethod
    def _timezone_name() -> str:
        now = time.localtime()
        index = 1 if now.tm_isdst > 0 and len(time.tzname) > 1 else 0
        name = time.tzname[index] if time.tzname else ""
        return name or "local"
