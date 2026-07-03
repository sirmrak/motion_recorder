"""Sensor platform for Motion Recorder."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, CONF_CAMERA_ENTITY


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Motion Recorder sensor."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MotionRecorderStatusSensor(coordinator, entry)])


class MotionRecorderStatusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Motion Recorder status sensor."""

    def __init__(self, coordinator, entry):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = f"{entry.title} Статус"
        self._attr_translation_key = "motion_recorder_status"
        
        # Добавляем информацию об устройстве
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Motion Recorder",
            model="Camera Recording Integration",
        )
        
        # Icon mapping
        self._icon_map = {
            "idle": "mdi:sleep",
            "detecting": "mdi:motion-sensor",
            "recording": "mdi:record-rec",
            "delaying": "mdi:timer-sand",
            "finalizing": "mdi:file-check",
            "completed": "mdi:check-circle",
            "error": "mdi:alert-circle",
            "disabled": "mdi:video-off", 
        }

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self.coordinator.state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self.coordinator.attributes

    @property
    def icon(self):
        """Return the icon."""
        return self._icon_map.get(self.coordinator.state, "mdi:camera")

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()