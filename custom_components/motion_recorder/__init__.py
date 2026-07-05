"""The Motion Recorder integration."""
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_LOG_LEVEL, DEFAULT_LOG_LEVEL
from .coordinator import MotionRecorderCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch"]


def _get_config_value(entry: ConfigEntry, key: str, default=None):
    """Get config value from options first, then from data, then default."""
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Motion Recorder from a config entry."""
    log_level = _get_config_value(entry, CONF_LOG_LEVEL, DEFAULT_LOG_LEVEL)
    logging.getLogger(f"custom_components.{DOMAIN}").setLevel(getattr(logging, log_level, logging.INFO))
    _LOGGER.info("Motion Recorder integration loaded with log level: %s", log_level)

    hass.data.setdefault(DOMAIN, {})

    coordinator = MotionRecorderCoordinator(hass, entry)
    await coordinator.async_setup()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration."""
    _LOGGER.info("Options updated, reloading Motion Recorder integration")
    await hass.config_entries.async_reload(entry.entry_id)