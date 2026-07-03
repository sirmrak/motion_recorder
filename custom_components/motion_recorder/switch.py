"""Switch platform for Motion Recorder."""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Motion Recorder switch."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MotionRecorderEnabledSwitch(coordinator, entry)])


class MotionRecorderEnabledSwitch(CoordinatorEntity, SwitchEntity, RestoreEntity):
    """Representation of a Motion Recorder enabled switch."""

    def __init__(self, coordinator, entry):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_name = f"{entry.title} Запись"
        self._attr_translation_key = "motion_recorder_enabled"
        self._attr_icon = "mdi:video"
        
        # Добавляем информацию об устройстве для привязки к Device Registry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Motion Recorder",
            model="Camera Recording Integration",
        )

    @property
    def is_on(self) -> bool:
        """Return True if recording is enabled."""
        return self.coordinator.is_enabled

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on recording."""
        _LOGGER.info("Switch turned ON, enabling recording")
        await self.coordinator.set_enabled(True)
        self.async_write_ha_state()  # Принудительно обновляем UI

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off recording."""
        _LOGGER.info("Switch turned OFF, disabling recording")
        await self.coordinator.set_enabled(False)
        self.async_write_ha_state()  # Принудительно обновляем UI

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        
        # Восстанавливаем последнее состояние из базы данных HA
        last_state = await self.async_get_last_state()
        if last_state is not None:
            is_enabled = last_state.state == STATE_ON
            _LOGGER.info("Restored switch state: %s", last_state.state)
            
            # Синхронизируем состояние с координатором
            if self.coordinator.is_enabled != is_enabled:
                await self.coordinator.set_enabled(is_enabled)
            
            # Принудительно обновляем UI после восстановления
            self.async_write_ha_state()
        else:
            _LOGGER.info("Switch added for the first time, defaulting to enabled")

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()