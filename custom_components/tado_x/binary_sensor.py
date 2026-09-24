"""Binary sensor platform for Tado X."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .heat_pump import get_path
from .coordinator import TadoXDataUpdateCoordinator, TadoXDevice, TadoXRoom

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TadoXRoomBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Tado X room binary sensor entity."""

    value_fn: Callable[[TadoXRoom], bool | None]


@dataclass(frozen=True, kw_only=True)
class TadoXDeviceBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Tado X device binary sensor entity."""

    value_fn: Callable[[TadoXDevice], bool | None]


ROOM_BINARY_SENSORS: tuple[TadoXRoomBinarySensorEntityDescription, ...] = (
    TadoXRoomBinarySensorEntityDescription(
        key="window_open",
        translation_key="window_open",
        device_class=BinarySensorDeviceClass.WINDOW,
        value_fn=lambda room: room.open_window_detected,
    ),
    TadoXRoomBinarySensorEntityDescription(
        key="heating",
        translation_key="heating",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda room: room.heating_power > 0,
    ),
    TadoXRoomBinarySensorEntityDescription(
        key="overlay_active",
        translation_key="overlay_active",
        icon="mdi:hand-back-left",
        value_fn=lambda room: room.manual_control_active,
    ),
)

DEVICE_BINARY_SENSORS: tuple[TadoXDeviceBinarySensorEntityDescription, ...] = (
    TadoXDeviceBinarySensorEntityDescription(
        key="connectivity",
        translation_key="connectivity",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda device: device.connection_state == "CONNECTED",
    ),
    TadoXDeviceBinarySensorEntityDescription(
        key="battery_low",
        translation_key="battery_low",
        device_class=BinarySensorDeviceClass.BATTERY,
        value_fn=lambda device: device.battery_state == "LOW" if device.battery_state else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X binary sensor entities."""
    coordinator: TadoXDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = []

    # Add room binary sensors
    for room_id in coordinator.data.rooms:
        for description in ROOM_BINARY_SENSORS:
            entities.append(TadoXRoomBinarySensor(coordinator, room_id, description))

    # Add device binary sensors
    for device in coordinator.data.devices.values():
        for description in DEVICE_BINARY_SENSORS:
            # Skip battery low for devices without batteries
            if description.key == "battery_low" and not device.battery_state:
                continue
            entities.append(TadoXDeviceBinarySensor(coordinator, device.serial_number, description))

    # Add heat pump optimizer binary sensors (only if /heatPump answers)
    if coordinator.data.has_heat_pump:
        for description in HEAT_PUMP_BINARY_SENSORS:
            entities.append(TadoXHeatPumpBinarySensor(coordinator, description))

    async_add_entities(entities)


class TadoXRoomBinarySensor(CoordinatorEntity[TadoXDataUpdateCoordinator], BinarySensorEntity):
    """Tado X room binary sensor entity."""

    _attr_has_entity_name = True
    entity_description: TadoXRoomBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TadoXDataUpdateCoordinator,
        room_id: int,
        description: TadoXRoomBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator)
        self._room_id = room_id
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.home_id}_{room_id}_{description.key}"

    @property
    def _room(self) -> TadoXRoom | None:
        """Get the room data."""
        return self.coordinator.data.rooms.get(self._room_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        room = self._room
        room_name = room.name if room else f"Room {self._room_id}"

        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.home_id}_{self._room_id}")},
            name=room_name,
            manufacturer="Tado",
            model="Tado X Room",
            via_device=(DOMAIN, str(self.coordinator.home_id)),
        )

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        room = self._room
        if not room:
            return None
        return self.entity_description.value_fn(room)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class TadoXDeviceBinarySensor(CoordinatorEntity[TadoXDataUpdateCoordinator], BinarySensorEntity):
    """Tado X device binary sensor entity."""

    _attr_has_entity_name = True
    entity_description: TadoXDeviceBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TadoXDataUpdateCoordinator,
        serial_number: str,
        description: TadoXDeviceBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self.entity_description = description
        self._attr_unique_id = f"{serial_number}_{description.key}"
        # Simple name without serial suffix - device name already has it

    @property
    def _device(self) -> TadoXDevice | None:
        """Get the device data."""
        return self.coordinator.data.devices.get(self._serial_number)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device = self._device
        if not device:
            return DeviceInfo(
                identifiers={(DOMAIN, self._serial_number)},
            )

        # French names for device types (used in device name)
        device_type_names_fr = {
            "VA04": "Vanne",
            "SU04": "Capteur Temp",
            "TR04": "Récepteur",  # Wireless Receiver X
            "RU04": "Thermostat",  # Wired Smart Thermostat X
            "IB02": "Bridge X",
        }

        # English model names (used in device model field)
        device_type_models = {
            "VA04": "Radiator Valve X",
            "SU04": "Temperature Sensor X",
            "TR04": "Wireless Receiver X",
            "RU04": "Wired Smart Thermostat X",
            "IB02": "Bridge X",
        }

        # Determine via_device - link to room if device has one, otherwise to home
        via_device_id = (
            (DOMAIN, f"{self.coordinator.home_id}_{device.room_id}")
            if device.room_id
            else (DOMAIN, str(self.coordinator.home_id))
        )

        # Generate device name with room name and numbering
        base_name = device_type_names_fr.get(device.device_type, device.device_type)

        if device.room_id and device.room_name:
            # Count devices of same type in same room to determine numbering
            same_type_in_room = sorted([
                d.serial_number for d in self.coordinator.data.devices.values()
                if d.room_id == device.room_id and d.device_type == device.device_type
            ])

            if len(same_type_in_room) > 1:
                # Multiple devices of same type - add number
                device_number = same_type_in_room.index(self._serial_number) + 1
                device_name = f"{base_name} {device_number} - {device.room_name}"
            else:
                # Only one device of this type - no number needed
                device_name = f"{base_name} - {device.room_name}"
        else:
            # No room - use serial number suffix (e.g., Bridge)
            device_name = f"{base_name} ({self._serial_number[-4:]})"

        return DeviceInfo(
            identifiers={(DOMAIN, self._serial_number)},
            name=device_name,
            manufacturer="Tado",
            model=device_type_models.get(device.device_type, device.device_type),
            sw_version=device.firmware_version,
            via_device=via_device_id,
        )

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor state."""
        device = self._device
        if not device:
            return None
        return self.entity_description.value_fn(device)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


@dataclass(frozen=True, kw_only=True)
class TadoXHeatPumpBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Tado X heat pump binary sensor entity."""

    path: str
    on_value: Any = True
    on_fn: Callable[[Any], bool] | None = None


HEAT_PUMP_BINARY_SENSORS: tuple[TadoXHeatPumpBinarySensorEntityDescription, ...] = (
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_dhw_tank_fully_loaded",
        name="Heat pump hot water tank fully loaded",
        icon="mdi:water-boiler-auto",
        path="domesticHotWater.tankIsFullyLoaded",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_dhw_manual_off",
        name="Heat pump hot water manual off",
        icon="mdi:water-boiler-off",
        path="domesticHotWater.manualOffActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_dhw_boost_active",
        name="Heat pump hot water boost active",
        icon="mdi:rocket-launch",
        path="domesticHotWater.boostActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_dhw_heating",
        name="Heat pump hot water heating",
        device_class=BinarySensorDeviceClass.HEAT,
        path="domesticHotWater.heatingActivityInPercent",
        on_fn=lambda v: v > 0,
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_heating",
        name="Heat pump space heating",
        device_class=BinarySensorDeviceClass.HEAT,
        path="heating.heatingActivityInPercent",
        on_fn=lambda v: v > 0,
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_load_shifting_active",
        name="Heat pump load shifting active",
        icon="mdi:transfer",
        path="heating.loadShiftingActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_standby_active",
        name="Heat pump standby active",
        icon="mdi:power-sleep",
        path="heating.standbyActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_heating_overlay_active",
        name="Heat pump heating overlay active",
        icon="mdi:layers",
        path="heating.overlayActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_room_guided_mode",
        name="Heat pump room guided mode",
        icon="mdi:home-thermometer",
        path="heating.roomGuidedModeActive",
    ),
    TadoXHeatPumpBinarySensorEntityDescription(
        key="heat_pump_connected",
        name="Heat pump connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        path="connection.state",
        on_value="CONNECTED",
    ),
)


class TadoXHeatPumpBinarySensor(CoordinatorEntity[TadoXDataUpdateCoordinator], BinarySensorEntity):
    """Tado X heat pump optimizer binary sensor (home device)."""

    _attr_has_entity_name = True
    entity_description: TadoXHeatPumpBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: TadoXDataUpdateCoordinator,
        description: TadoXHeatPumpBinarySensorEntityDescription,
    ) -> None:
        """Initialize heat pump binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.home_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the home."""
        return DeviceInfo(identifiers={(DOMAIN, str(self.coordinator.home_id))})

    @property
    def available(self) -> bool:
        """Return True while /heatPump answers and carries the field."""
        data = self.coordinator.data
        return (
            data is not None
            and data.has_heat_pump
            and get_path(data.heat_pump_raw, self.entity_description.path) is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the field equals the 'on' value."""
        value = get_path(self.coordinator.data.heat_pump_raw, self.entity_description.path)
        if value is None:
            return None
        if self.entity_description.on_fn:
            return self.entity_description.on_fn(value)
        return value == self.entity_description.on_value
