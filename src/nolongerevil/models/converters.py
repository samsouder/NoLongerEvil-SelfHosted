"""Converters between dataclasses and SQLModel models."""

import json
from datetime import datetime

from nolongerevil.lib.types import (
    APIKey,
    APIKeyPermissions,
    DeviceObject,
    DeviceOwner,
    DeviceShare,
    DeviceShareInvite,
    DeviceShareInviteStatus,
    DeviceSharePermission,
    EntryKey,
    HvacUsageSegment,
    HvacUsageState,
    IntegrationConfig,
    UserInfo,
    WeatherData,
)
from nolongerevil.models.auth import APIKeyModel
from nolongerevil.models.base import ms_to_timestamp, now_ms, timestamp_to_ms
from nolongerevil.models.device import DeviceObjectModel, HvacUsageSegmentModel
from nolongerevil.models.integration import IntegrationConfigModel, WeatherDataModel
from nolongerevil.models.sharing import DeviceShareInviteModel, DeviceShareModel
from nolongerevil.models.user import DeviceOwnerModel, EntryKeyModel, UserInfoModel

# Device Object Converters


def device_object_to_model(obj: DeviceObject) -> DeviceObjectModel:
    """Convert DeviceObject dataclass to SQLModel."""
    return DeviceObjectModel(
        serial=obj.serial,
        object_key=obj.object_key,
        object_revision=obj.object_revision,
        object_timestamp=obj.object_timestamp,
        value=json.dumps(obj.value),
        updatedAt=timestamp_to_ms(obj.updated_at) or now_ms(),
    )


def model_to_device_object(model: DeviceObjectModel) -> DeviceObject:
    """Convert SQLModel to DeviceObject dataclass."""
    return DeviceObject(
        serial=model.serial,
        object_key=model.object_key,
        object_revision=model.object_revision,
        object_timestamp=model.object_timestamp,
        value=json.loads(model.value),
        updated_at=ms_to_timestamp(model.updatedAt) or datetime.now(),
    )


def hvac_usage_segment_to_model(segment: HvacUsageSegment) -> HvacUsageSegmentModel:
    """Convert HVAC usage segment dataclass to SQLModel."""
    return HvacUsageSegmentModel(
        id=segment.id,
        serial=segment.serial,
        state=segment.state.value,
        started_at=timestamp_to_ms(segment.started_at) or now_ms(),
        last_observed_at=timestamp_to_ms(segment.last_observed_at) or now_ms(),
        ended_at=timestamp_to_ms(segment.ended_at),
    )


def model_to_hvac_usage_segment(model: HvacUsageSegmentModel) -> HvacUsageSegment:
    """Convert SQLModel to HVAC usage segment dataclass."""
    return HvacUsageSegment(
        id=model.id,
        serial=model.serial,
        state=HvacUsageState(model.state),
        started_at=ms_to_timestamp(model.started_at) or datetime.now(),
        last_observed_at=ms_to_timestamp(model.last_observed_at) or datetime.now(),
        ended_at=ms_to_timestamp(model.ended_at),
    )


# User Info Converters


def user_info_to_model(user: UserInfo) -> UserInfoModel:
    """Convert UserInfo dataclass to SQLModel."""
    return UserInfoModel(
        clerkId=user.clerk_id,
        email=user.email,
        createdAt=timestamp_to_ms(user.created_at) or now_ms(),
    )


def model_to_user_info(model: UserInfoModel) -> UserInfo:
    """Convert SQLModel to UserInfo dataclass."""
    return UserInfo(
        clerk_id=model.clerkId,
        email=model.email,
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
    )


# Entry Key Converters


def entry_key_to_model(entry_key: EntryKey) -> EntryKeyModel:
    """Convert EntryKey dataclass to SQLModel."""
    return EntryKeyModel(
        code=entry_key.code,
        serial=entry_key.serial,
        createdAt=timestamp_to_ms(entry_key.created_at) or now_ms(),
        expiresAt=timestamp_to_ms(entry_key.expires_at) or now_ms(),
        claimedBy=entry_key.claimed_by,
        claimedAt=timestamp_to_ms(entry_key.claimed_at),
    )


def model_to_entry_key(model: EntryKeyModel) -> EntryKey:
    """Convert SQLModel to EntryKey dataclass."""
    return EntryKey(
        code=model.code,
        serial=model.serial,
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
        expires_at=ms_to_timestamp(model.expiresAt) or datetime.now(),
        claimed_by=model.claimedBy,
        claimed_at=ms_to_timestamp(model.claimedAt),
    )


# Device Owner Converters


def device_owner_to_model(owner: DeviceOwner) -> DeviceOwnerModel:
    """Convert DeviceOwner dataclass to SQLModel."""
    return DeviceOwnerModel(
        serial=owner.serial,
        userId=owner.user_id,
        createdAt=timestamp_to_ms(owner.created_at) or now_ms(),
    )


def model_to_device_owner(model: DeviceOwnerModel) -> DeviceOwner:
    """Convert SQLModel to DeviceOwner dataclass."""
    return DeviceOwner(
        serial=model.serial,
        user_id=model.userId,
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
    )


# Weather Data Converters


def weather_data_to_model(weather: WeatherData) -> WeatherDataModel:
    """Convert WeatherData dataclass to SQLModel."""
    return WeatherDataModel(
        postalCode=weather.postal_code,
        country=weather.country,
        fetchedAt=timestamp_to_ms(weather.fetched_at) or now_ms(),
        data=json.dumps(weather.data),
    )


def model_to_weather_data(model: WeatherDataModel) -> WeatherData:
    """Convert SQLModel to WeatherData dataclass."""
    return WeatherData(
        postal_code=model.postalCode,
        country=model.country,
        fetched_at=ms_to_timestamp(model.fetchedAt) or datetime.now(),
        data=json.loads(model.data),
    )


# API Key Converters


def api_key_to_model(api_key: APIKey) -> APIKeyModel:
    """Convert APIKey dataclass to SQLModel."""
    permissions_json = json.dumps(
        {
            "devices": api_key.permissions.devices,
            "scopes": api_key.permissions.scopes,
        }
    )
    return APIKeyModel(
        id=int(api_key.id) if api_key.id else None,
        keyHash=api_key.key_hash,
        keyPreview=api_key.key_preview,
        userId=api_key.user_id,
        name=api_key.name,
        permissions=permissions_json,
        createdAt=timestamp_to_ms(api_key.created_at) or now_ms(),
        expiresAt=timestamp_to_ms(api_key.expires_at),
        lastUsedAt=timestamp_to_ms(api_key.last_used_at),
    )


def model_to_api_key(model: APIKeyModel) -> APIKey:
    """Convert SQLModel to APIKey dataclass."""
    permissions_data = json.loads(model.permissions)
    return APIKey(
        id=str(model.id),
        key_hash=model.keyHash,
        key_preview=model.keyPreview,
        user_id=model.userId,
        name=model.name,
        permissions=APIKeyPermissions(
            devices=permissions_data.get("devices", []),
            scopes=permissions_data.get("scopes", ["read", "write"]),
        ),
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
        expires_at=ms_to_timestamp(model.expiresAt),
        last_used_at=ms_to_timestamp(model.lastUsedAt),
    )


# Device Share Converters


def device_share_to_model(share: DeviceShare) -> DeviceShareModel:
    """Convert DeviceShare dataclass to SQLModel."""
    return DeviceShareModel(
        ownerId=share.owner_id,
        sharedWithUserId=share.shared_with_user_id,
        serial=share.serial,
        permissions=share.permissions.value,
        createdAt=timestamp_to_ms(share.created_at) or now_ms(),
    )


def model_to_device_share(model: DeviceShareModel) -> DeviceShare:
    """Convert SQLModel to DeviceShare dataclass."""
    return DeviceShare(
        owner_id=model.ownerId,
        shared_with_user_id=model.sharedWithUserId,
        serial=model.serial,
        permissions=DeviceSharePermission(model.permissions),
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
    )


# Device Share Invite Converters


def device_share_invite_to_model(invite: DeviceShareInvite) -> DeviceShareInviteModel:
    """Convert DeviceShareInvite dataclass to SQLModel."""
    return DeviceShareInviteModel(
        inviteToken=invite.invite_token,
        ownerId=invite.owner_id,
        email=invite.email,
        serial=invite.serial,
        permissions=invite.permissions.value,
        status=invite.status.value,
        invitedAt=timestamp_to_ms(invite.invited_at) or now_ms(),
        expiresAt=timestamp_to_ms(invite.expires_at) or now_ms(),
        acceptedAt=timestamp_to_ms(invite.accepted_at),
        sharedWithUserId=invite.shared_with_user_id,
    )


def model_to_device_share_invite(model: DeviceShareInviteModel) -> DeviceShareInvite:
    """Convert SQLModel to DeviceShareInvite dataclass."""
    return DeviceShareInvite(
        invite_token=model.inviteToken,
        owner_id=model.ownerId,
        email=model.email,
        serial=model.serial,
        permissions=DeviceSharePermission(model.permissions),
        status=DeviceShareInviteStatus(model.status),
        invited_at=ms_to_timestamp(model.invitedAt) or datetime.now(),
        expires_at=ms_to_timestamp(model.expiresAt) or datetime.now(),
        accepted_at=ms_to_timestamp(model.acceptedAt),
        shared_with_user_id=model.sharedWithUserId,
    )


# Integration Config Converters


def integration_config_to_model(integration: IntegrationConfig) -> IntegrationConfigModel:
    """Convert IntegrationConfig dataclass to SQLModel."""
    return IntegrationConfigModel(
        userId=integration.user_id,
        type=integration.type,
        enabled=1 if integration.enabled else 0,
        config=json.dumps(integration.config),
        createdAt=timestamp_to_ms(integration.created_at) or now_ms(),
        updatedAt=timestamp_to_ms(integration.updated_at) or now_ms(),
    )


def model_to_integration_config(model: IntegrationConfigModel) -> IntegrationConfig:
    """Convert SQLModel to IntegrationConfig dataclass."""
    return IntegrationConfig(
        user_id=model.userId,
        type=model.type,
        enabled=bool(model.enabled),
        config=json.loads(model.config),
        created_at=ms_to_timestamp(model.createdAt) or datetime.now(),
        updated_at=ms_to_timestamp(model.updatedAt) or datetime.now(),
    )
