"""Device-related SQLModel models."""

from sqlalchemy import Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class DeviceObjectModel(SQLModel, table=True):
    """Device state object stored in the 'states' table."""

    __tablename__ = "states"

    # Composite primary key (serial, object_key)
    serial: str = Field(primary_key=True)
    object_key: str = Field(primary_key=True)
    object_revision: int
    object_timestamp: int
    value: str = Field(sa_column=Column(Text))  # JSON stored as text
    updatedAt: int  # Millisecond timestamp

    __table_args__ = (Index("idx_states_serial", "serial"),)


class SessionModel(SQLModel, table=True):
    """Device connection session stored in the 'sessions' table."""

    __tablename__ = "sessions"

    serial: str = Field(primary_key=True)
    session: str = Field(primary_key=True)
    endpoint: str
    startedAt: int  # Millisecond timestamp
    lastActivity: int  # Millisecond timestamp
    open: int  # Boolean as integer (0/1)
    client: str | None = None
    meta: str | None = Field(default=None, sa_column=Column(Text))  # JSON as text

    __table_args__ = (Index("idx_sessions_serial", "serial"),)


class LogModel(SQLModel, table=True):
    """Request/response log stored in the 'logs' table."""

    __tablename__ = "logs"

    # SQLModel requires a primary key, but logs table doesn't have one
    # We'll add an auto-increment id column
    id: int | None = Field(default=None, primary_key=True)
    ts: int  # Millisecond timestamp
    route: str
    serial: str | None = None
    req: str = Field(sa_column=Column(Text))  # JSON as text
    res: str = Field(sa_column=Column(Text))  # JSON as text

    __table_args__ = (
        Index("idx_logs_serial", "serial"),
        Index("idx_logs_ts", "ts"),
    )


class HvacUsageSegmentModel(SQLModel, table=True):
    """HVAC runtime segment stored in the 'hvac_usage_segments' table."""

    __tablename__ = "hvac_usage_segments"

    id: int | None = Field(default=None, primary_key=True)
    serial: str
    state: str
    started_at: int
    last_observed_at: int
    ended_at: int | None = None

    __table_args__ = (
        Index("idx_hvac_usage_serial_started_at", "serial", "started_at"),
        Index("idx_hvac_usage_serial_state_ended_at", "serial", "state", "ended_at"),
    )


class ThermostatStateSnapshotModel(SQLModel, table=True):
    """Thermostat context snapshot stored with usage history."""

    __tablename__ = "thermostat_state_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    serial: str
    captured_at: int
    current_temperature: float | None = None
    target_temperature: float | None = None
    target_temperature_high: float | None = None
    target_temperature_low: float | None = None
    humidity: float | None = None
    hvac_mode: str | None = None
    eco_mode: str | None = None
    away: bool | None = None
    is_online: bool | None = None

    __table_args__ = (
        Index("idx_thermostat_snapshots_serial_captured", "serial", "captured_at"),
    )


class HvacUsageDailyRollupModel(SQLModel, table=True):
    """Daily HVAC runtime rollup stored for read-optimized history views."""

    __tablename__ = "hvac_usage_daily_rollups"

    id: int | None = Field(default=None, primary_key=True)
    serial: str
    timezone: str
    day: str
    state: str
    total_seconds: int = 0
    run_count: int = 0
    longest_run_seconds: int = 0
    updated_at: int

    __table_args__ = (
        UniqueConstraint(
            "serial",
            "timezone",
            "day",
            "state",
            name="uq_hvac_usage_daily_rollup",
        ),
        Index("idx_hvac_rollups_serial_tz_day", "serial", "timezone", "day"),
    )
