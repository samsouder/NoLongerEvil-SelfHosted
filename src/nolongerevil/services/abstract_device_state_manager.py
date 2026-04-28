"""Abstract base class for device state persistence."""

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Any

from nolongerevil.lib.types import (
    APIKey,
    DeviceObject,
    DeviceOwner,
    DeviceShare,
    DeviceShareInvite,
    EntryKey,
    HvacUsageDailyRollup,
    HvacUsageSegment,
    IntegrationConfig,
    ThermostatStateSnapshot,
    UserInfo,
    WeatherData,
)


class AbstractDeviceStateManager(ABC):
    """Abstract base class for device state persistence."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the storage backend."""
        pass

    # Device state operations
    @abstractmethod
    async def get_object(self, serial: str, object_key: str) -> DeviceObject | None:
        """Get a single device object by serial and key."""
        pass

    @abstractmethod
    async def get_objects_by_serial(self, serial: str) -> list[DeviceObject]:
        """Get all objects for a device."""
        pass

    @abstractmethod
    async def get_all_objects(self) -> list[DeviceObject]:
        """Get all device objects."""
        pass

    @abstractmethod
    async def upsert_object(self, obj: DeviceObject) -> None:
        """Insert or update a device object."""
        pass

    @abstractmethod
    async def delete_object(self, serial: str, object_key: str) -> bool:
        """Delete a device object."""
        pass

    @abstractmethod
    async def delete_device(self, serial: str) -> int:
        """Delete all objects for a device.

        Args:
            serial: Device serial

        Returns:
            Number of objects deleted
        """
        pass

    # HVAC usage history operations
    @abstractmethod
    async def create_hvac_usage_segment(self, segment: HvacUsageSegment) -> HvacUsageSegment:
        """Create a new HVAC usage segment."""
        pass

    @abstractmethod
    async def update_hvac_usage_segment(
        self,
        segment_id: int,
        *,
        last_observed_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> HvacUsageSegment | None:
        """Update an HVAC usage segment."""
        pass

    @abstractmethod
    async def get_open_hvac_usage_segments(self) -> list[HvacUsageSegment]:
        """Get all currently open HVAC usage segments."""
        pass

    @abstractmethod
    async def list_hvac_usage_segments(
        self,
        serial: str,
        range_start: datetime,
        range_end: datetime,
    ) -> list[HvacUsageSegment]:
        """List HVAC usage segments overlapping the requested range."""
        pass

    @abstractmethod
    async def close_stale_hvac_usage_segments(self) -> int:
        """Close open HVAC usage segments at their last observed timestamp."""
        pass

    @abstractmethod
    async def prune_hvac_usage_segments(self, older_than: datetime) -> int:
        """Delete ended HVAC usage segments older than the cutoff."""
        pass

    @abstractmethod
    async def get_hvac_usage_bounds(self, serial: str) -> tuple[datetime, datetime] | None:
        """Get earliest and latest HVAC usage timestamps for a serial."""
        pass

    @abstractmethod
    async def create_thermostat_state_snapshot(
        self,
        snapshot: ThermostatStateSnapshot,
    ) -> ThermostatStateSnapshot:
        """Create a thermostat state snapshot."""
        pass

    @abstractmethod
    async def get_latest_thermostat_state_snapshot(
        self,
        serial: str,
    ) -> ThermostatStateSnapshot | None:
        """Get the latest thermostat state snapshot for a serial."""
        pass

    @abstractmethod
    async def list_thermostat_state_snapshots(
        self,
        serial: str,
        range_start: datetime,
        range_end: datetime,
    ) -> list[ThermostatStateSnapshot]:
        """List thermostat state snapshots in a time range."""
        pass

    @abstractmethod
    async def upsert_hvac_usage_daily_rollup(self, rollup: HvacUsageDailyRollup) -> HvacUsageDailyRollup:
        """Insert or update a daily HVAC usage rollup."""
        pass

    @abstractmethod
    async def list_hvac_usage_daily_rollups(
        self,
        serial: str,
        timezone: str,
        start_day: date,
        end_day: date,
    ) -> list[HvacUsageDailyRollup]:
        """List daily HVAC usage rollups for a date range."""
        pass

    # Entry key operations
    @abstractmethod
    async def create_entry_key(self, entry_key: EntryKey) -> None:
        """Create a new entry key for device pairing."""
        pass

    @abstractmethod
    async def get_entry_key(self, code: str) -> EntryKey | None:
        """Get an entry key by code."""
        pass

    @abstractmethod
    async def get_entry_key_by_serial(self, serial: str) -> EntryKey | None:
        """Get an unexpired entry key by serial."""
        pass

    @abstractmethod
    async def get_latest_entry_key_by_serial(self, serial: str) -> EntryKey | None:
        """Get the most recent entry key by serial (including expired/claimed)."""
        pass

    @abstractmethod
    async def claim_entry_key(self, code: str, user_id: str) -> bool:
        """Claim an entry key for a user."""
        pass

    # User operations
    @abstractmethod
    async def create_user(self, user: UserInfo) -> None:
        """Create a new user."""
        pass

    @abstractmethod
    async def get_user(self, clerk_id: str) -> UserInfo | None:
        """Get a user by clerk ID."""
        pass

    @abstractmethod
    async def get_user_by_email(self, email: str) -> UserInfo | None:
        """Get a user by email."""
        pass

    # Device owner operations
    @abstractmethod
    async def set_device_owner(self, owner: DeviceOwner) -> None:
        """Set the owner of a device."""
        pass

    @abstractmethod
    async def get_device_owner(self, serial: str) -> DeviceOwner | None:
        """Get the owner of a device."""
        pass

    @abstractmethod
    async def get_user_devices(self, user_id: str) -> list[str]:
        """Get all device serials owned by a user."""
        pass

    @abstractmethod
    async def delete_device_owner(self, serial: str, user_id: str) -> bool:
        """Delete device ownership record.

        Args:
            serial: Device serial
            user_id: Owner user ID

        Returns:
            True if deleted, False if not found
        """
        pass

    # Weather operations
    @abstractmethod
    async def get_cached_weather(self, postal_code: str, country: str) -> WeatherData | None:
        """Get cached weather data."""
        pass

    @abstractmethod
    async def cache_weather(self, weather: WeatherData) -> None:
        """Cache weather data."""
        pass

    # API key operations
    @abstractmethod
    async def create_api_key(self, api_key: APIKey) -> None:
        """Create a new API key."""
        pass

    @abstractmethod
    async def get_api_key_by_hash(self, key_hash: str) -> APIKey | None:
        """Get an API key by its hash."""
        pass

    @abstractmethod
    async def update_api_key_last_used(self, key_id: str) -> None:
        """Update the last used timestamp of an API key."""
        pass

    @abstractmethod
    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key."""
        pass

    @abstractmethod
    async def get_user_api_keys(self, user_id: str) -> list[APIKey]:
        """Get all API keys for a user."""
        pass

    # Device sharing operations
    @abstractmethod
    async def create_device_share(self, share: DeviceShare) -> None:
        """Create a device share."""
        pass

    @abstractmethod
    async def get_device_shares(self, serial: str) -> list[DeviceShare]:
        """Get all shares for a device."""
        pass

    @abstractmethod
    async def get_user_shared_devices(self, user_id: str) -> list[DeviceShare]:
        """Get all devices shared with a user."""
        pass

    @abstractmethod
    async def delete_device_share(
        self, owner_id: str, shared_with_user_id: str, serial: str
    ) -> bool:
        """Delete a device share."""
        pass

    # Device share invite operations
    @abstractmethod
    async def create_device_share_invite(self, invite: DeviceShareInvite) -> None:
        """Create a device share invitation."""
        pass

    @abstractmethod
    async def get_device_share_invite(self, invite_token: str) -> DeviceShareInvite | None:
        """Get an invitation by token."""
        pass

    @abstractmethod
    async def accept_device_share_invite(self, invite_token: str, user_id: str) -> bool:
        """Accept a device share invitation."""
        pass

    # Integration operations
    @abstractmethod
    async def get_integrations(self, user_id: str) -> list[IntegrationConfig]:
        """Get all integrations for a user."""
        pass

    @abstractmethod
    async def get_enabled_integrations(self) -> list[IntegrationConfig]:
        """Get all enabled integrations."""
        pass

    @abstractmethod
    async def upsert_integration(self, integration: IntegrationConfig) -> None:
        """Create or update an integration."""
        pass

    @abstractmethod
    async def delete_integration(self, user_id: str, integration_type: str) -> bool:
        """Delete an integration."""
        pass

    # Session logging
    @abstractmethod
    async def log_session(
        self,
        serial: str,
        session_id: str,
        endpoint: str,
        client: str | None,
        meta: dict[str, Any] | None,
    ) -> None:
        """Log a device session."""
        pass

    @abstractmethod
    async def update_session_activity(self, serial: str, session_id: str) -> None:
        """Update the last activity timestamp for a session."""
        pass

    @abstractmethod
    async def close_session(self, serial: str, session_id: str) -> None:
        """Mark a session as closed."""
        pass

    # Request logging
    @abstractmethod
    async def log_request(
        self,
        route: str,
        serial: str | None,
        request_data: dict[str, Any],
        response_data: dict[str, Any],
    ) -> None:
        """Log a request/response pair."""
        pass

    # Methods from TypeScript AbstractDeviceStateManager

    @abstractmethod
    async def generate_entry_key(
        self, serial: str, ttl_seconds: int = 3600
    ) -> dict[str, Any] | None:
        """Generate entry key for device pairing.

        Args:
            serial: Device serial number
            ttl_seconds: Time-to-live in seconds

        Returns:
            Entry key data with code and expiration, or None on failure
        """
        pass

    @abstractmethod
    async def update_user_away_status(self, user_id: str) -> None:
        """Update user away status based on device state.

        Args:
            user_id: User ID
        """
        pass

    @abstractmethod
    async def sync_user_weather_from_device(self, user_id: str) -> None:
        """Sync user weather from device postal code.

        Args:
            user_id: User ID
        """
        pass

    @abstractmethod
    async def ensure_device_alert_dialog(self, serial: str) -> None:
        """Ensure device alert dialog exists.

        Args:
            serial: Device serial
        """
        pass

    @abstractmethod
    async def get_user_weather(self, user_id: str) -> dict[str, Any] | None:
        """Get user's weather data.

        Args:
            user_id: User ID

        Returns:
            Weather data or None
        """
        pass

    @abstractmethod
    async def get_all_enabled_mqtt_integrations(
        self,
    ) -> list[dict[str, Any]]:
        """Get all enabled MQTT integrations for loading by IntegrationManager.

        Returns:
            List of {userId, config} dicts
        """
        pass

    @abstractmethod
    async def validate_api_key(self, key: str) -> dict[str, Any] | None:
        """Validate API key for authentication.

        Args:
            key: Raw API key

        Returns:
            {userId, permissions, keyId} or None if invalid
        """
        pass

    @abstractmethod
    async def check_api_key_permission(
        self,
        user_id: str,
        serial: str,
        required_scopes: list[str],
        permissions: dict[str, Any],
    ) -> bool:
        """Check if API key has permission to access a device.

        Args:
            user_id: User ID
            serial: Device serial
            required_scopes: Required scopes
            permissions: API key permissions

        Returns:
            True if permitted
        """
        pass

    @abstractmethod
    async def list_user_devices(self, user_id: str) -> list[dict[str, str]]:
        """List all devices owned by a user.

        Args:
            user_id: User ID

        Returns:
            List of {serial} dicts
        """
        pass

    @abstractmethod
    async def get_shared_with_me(self, user_id: str) -> list[dict[str, Any]]:
        """Get devices shared with a user.

        Args:
            user_id: User ID

        Returns:
            List of {serial, permissions} dicts
        """
        pass
