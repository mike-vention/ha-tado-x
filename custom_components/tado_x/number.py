"""Number platform for Tado X."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DHW_MAX_TEMP, DHW_MIN_TEMP, DOMAIN
from .coordinator import TadoXDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tado X number entities."""
    coordinator: TadoXDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []

    # Only add flow temperature if the feature is available
    if coordinator.data and coordinator.data.has_flow_temp_control:
        entities.append(TadoXMaxFlowTemperature(coordinator))

    # Only add DHW temperature if a heat pump optimizer exposes DHW
    if coordinator.data and coordinator.data.has_heat_pump_dhw:
        entities.append(TadoXHeatPumpDhwTemperature(coordinator))

    async_add_entities(entities)


class TadoXMaxFlowTemperature(CoordinatorEntity[TadoXDataUpdateCoordinator], NumberEntity):
    """Number entity for Tado X max flow temperature."""

    _attr_has_entity_name = True
    _attr_translation_key = "max_flow_temperature"
    _attr_icon = "mdi:thermometer-water"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: TadoXDataUpdateCoordinator) -> None:
        """Initialize the max flow temperature entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.home_id}_max_flow_temperature"

        # Set min/max from constraints
        data = coordinator.data
        if data:
            self._attr_native_min_value = float(data.flow_temp_min or 20)
            self._attr_native_max_value = float(data.flow_temp_max or 75)
        else:
            self._attr_native_min_value = 20.0
            self._attr_native_max_value = 75.0
        self._attr_native_step = 1.0

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the home."""
        home_name = self.coordinator.home_name or f"Tado Home {self.coordinator.home_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, str(self.coordinator.home_id))},
            name=home_name,
            manufacturer="Tado",
            model="Tado X Home",
        )

    @property
    def native_value(self) -> float | None:
        """Return the current max flow temperature."""
        data = self.coordinator.data
        if not data or not data.has_flow_temp_control:
            return None
        return float(data.max_flow_temperature) if data.max_flow_temperature else None

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        data = self.coordinator.data
        return data is not None and data.has_flow_temp_control

    async def async_set_native_value(self, value: float) -> None:
        """Set the max flow temperature."""
        try:
            await self.coordinator.api.set_max_flow_temperature(int(value))
            await self.coordinator.async_request_refresh()
            _LOGGER.info("Set max flow temperature to %d°C", int(value))
        except Exception as err:
            _LOGGER.error("Failed to set max flow temperature: %s", err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update min/max if constraints changed
        data = self.coordinator.data
        if data and data.has_flow_temp_control:
            if data.flow_temp_min is not None:
                self._attr_native_min_value = float(data.flow_temp_min)
            if data.flow_temp_max is not None:
                self._attr_native_max_value = float(data.flow_temp_max)
        self.async_write_ha_state()


class TadoXHeatPumpDhwTemperature(CoordinatorEntity[TadoXDataUpdateCoordinator], NumberEntity):
    """Number entity for the heat pump domestic hot water target temperature.

    Changing the value rewrites targetSetpointValue in the Tado DHW schedule,
    so it persists until changed again (it is not a temporary override).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "dhw_temperature"
    _attr_icon = "mdi:water-boiler"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1.0

    def __init__(self, coordinator: TadoXDataUpdateCoordinator) -> None:
        """Initialize the DHW temperature entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.home_id}_heat_pump_dhw_temperature"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the home."""
        home_name = self.coordinator.home_name or f"Tado Home {self.coordinator.home_id}"
        return DeviceInfo(
            identifiers={(DOMAIN, str(self.coordinator.home_id))},
            name=home_name,
            manufacturer="Tado",
            model="Tado X Home",
        )

    @property
    def native_min_value(self) -> float:
        """Return the minimum allowed by the heat pump (API constraints)."""
        data = self.coordinator.data
        if data and data.dhw_min is not None:
            return float(data.dhw_min)
        return float(DHW_MIN_TEMP)

    @property
    def native_max_value(self) -> float:
        """Return the maximum allowed by the heat pump (API constraints)."""
        data = self.coordinator.data
        if data and data.dhw_max is not None:
            return float(data.dhw_max)
        return float(DHW_MAX_TEMP)

    @property
    def native_value(self) -> float | None:
        """Return the current DHW target temperature."""
        data = self.coordinator.data
        if not data or not data.has_heat_pump_dhw:
            return None
        return data.dhw_target_temperature

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        data = self.coordinator.data
        return data is not None and data.has_heat_pump_dhw

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the raw DHW state and schedule for diagnostics."""
        data = self.coordinator.data
        if not data or not data.dhw_raw:
            return {}
        return dict(data.dhw_raw)

    async def async_set_native_value(self, value: float) -> None:
        """Set the DHW target temperature."""
        try:
            await self.coordinator.api.set_heat_pump_dhw_temperature(int(value))
        except Exception as err:
            _LOGGER.error("Failed to set heat pump DHW temperature: %s", err)
            raise HomeAssistantError(f"Failed to set DHW temperature: {err}") from err
        _LOGGER.info("Set heat pump DHW temperature to %d°C", int(value))
        self.coordinator.invalidate_dhw()
        await self.coordinator.async_request_refresh()
