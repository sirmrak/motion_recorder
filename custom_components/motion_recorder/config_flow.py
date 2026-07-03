"""Config flow for Motion Recorder."""
from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_CAMERA_ENTITY,
    CONF_SAVE_PATH,
    CONF_FILENAME_TEMPLATE,
    CONF_MAX_DURATION,
    CONF_PREBUFFER,
    CONF_MOTION_SENSORS,
    CONF_MOTION_FILTER,
    CONF_OFF_DELAY,
    CONF_FORCE_STOP_SENSOR,
    CONF_FORCE_STOP_STATE,
    CONF_LOG_LEVEL,
    CONF_RETENTION_DAYS,
    DEFAULT_NAME,
    DEFAULT_SAVE_PATH,
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_MAX_DURATION,
    DEFAULT_PREBUFFER,
    DEFAULT_MOTION_FILTER,
    DEFAULT_OFF_DELAY,
    DEFAULT_FORCE_STOP_STATE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_RETENTION_DAYS,
)


# Список шаблонов имени файла (все с секундами для уникальности)
FILENAME_TEMPLATE_OPTIONS = [
    selector.SelectOptionDict(value="%d-%m-%Y_%H-%M-%S", label="ДД-ММ-ГГГГ_ЧЧ-ММ-СС (02-07-2026_20-30-45)"),
    selector.SelectOptionDict(value="%Y-%m-%d_%H-%M-%S", label="ГГГГ-ММ-ДД_ЧЧ-ММ-СС (2026-07-02_20-30-45)"),
    selector.SelectOptionDict(value="%d.%m.%Y_%H-%M-%S", label="ДД.ММ.ГГГГ_ЧЧ-ММ-СС (02.07.2026_20-30-45)"),
    selector.SelectOptionDict(value="%Y%m%d_%H%M%S", label="ГГГГММДД_ЧЧММСС (20260702_203045)"),
    selector.SelectOptionDict(value="%d.%m.%Y_%H.%M.%S", label="ДД.ММ.ГГГГ_ЧЧ.ММ.СС (все точки)"),
]


class MotionRecorderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Motion Recorder."""
    
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CAMERA_ENTITY])
            self._abort_if_unique_id_configured()
            
            return self.async_create_entry(
                title=user_input.get("name", DEFAULT_NAME),
                data=user_input,
            )
        
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("name", default=DEFAULT_NAME): str,
                vol.Required(CONF_CAMERA_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="camera")
                ),
                vol.Required(CONF_SAVE_PATH, default=DEFAULT_SAVE_PATH): str,
                vol.Required(CONF_FILENAME_TEMPLATE, default=DEFAULT_FILENAME_TEMPLATE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=FILENAME_TEMPLATE_OPTIONS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_MAX_DURATION, default=DEFAULT_MAX_DURATION): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=3600, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(CONF_PREBUFFER, default=DEFAULT_PREBUFFER): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(CONF_MOTION_SENSORS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
                ),
                vol.Required(CONF_MOTION_FILTER, default=DEFAULT_MOTION_FILTER): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=10, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(CONF_OFF_DELAY, default=DEFAULT_OFF_DELAY): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(CONF_RETENTION_DAYS, default=DEFAULT_RETENTION_DAYS): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=365, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="дн",
                    )
                ),
                vol.Optional(CONF_FORCE_STOP_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["binary_sensor", "switch", "input_boolean"],
                        multiple=False,
                    )
                ),
                vol.Required(CONF_FORCE_STOP_STATE, default=DEFAULT_FORCE_STOP_STATE): str,
                vol.Required(CONF_LOG_LEVEL, default=DEFAULT_LOG_LEVEL): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="DEBUG", label="DEBUG (подробно)"),
                            selector.SelectOptionDict(value="INFO", label="INFO (обычный)"),
                            selector.SelectOptionDict(value="WARNING", label="WARNING (только предупреждения)"),
                            selector.SelectOptionDict(value="ERROR", label="ERROR (только ошибки)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return MotionRecorderOptionsFlow()


class MotionRecorderOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Motion Recorder."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_FILENAME_TEMPLATE,
                    default=self.config_entry.options.get(CONF_FILENAME_TEMPLATE,
                            self.config_entry.data.get(CONF_FILENAME_TEMPLATE, DEFAULT_FILENAME_TEMPLATE))
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=FILENAME_TEMPLATE_OPTIONS,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_MAX_DURATION,
                    default=self.config_entry.options.get(CONF_MAX_DURATION, 
                            self.config_entry.data.get(CONF_MAX_DURATION, DEFAULT_MAX_DURATION))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=3600, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(
                    CONF_PREBUFFER,
                    default=self.config_entry.options.get(CONF_PREBUFFER,
                            self.config_entry.data.get(CONF_PREBUFFER, DEFAULT_PREBUFFER))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(
                    CONF_MOTION_FILTER,
                    default=self.config_entry.options.get(CONF_MOTION_FILTER,
                            self.config_entry.data.get(CONF_MOTION_FILTER, DEFAULT_MOTION_FILTER))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=10, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(
                    CONF_OFF_DELAY,
                    default=self.config_entry.options.get(CONF_OFF_DELAY,
                            self.config_entry.data.get(CONF_OFF_DELAY, DEFAULT_OFF_DELAY))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=60, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="сек",
                    )
                ),
                vol.Required(
                    CONF_RETENTION_DAYS,
                    default=self.config_entry.options.get(CONF_RETENTION_DAYS,
                            self.config_entry.data.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=365, step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="дн",
                    )
                ),
                vol.Required(
                    CONF_LOG_LEVEL,
                    default=self.config_entry.options.get(CONF_LOG_LEVEL,
                            self.config_entry.data.get(CONF_LOG_LEVEL, DEFAULT_LOG_LEVEL))
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="DEBUG", label="DEBUG (подробно)"),
                            selector.SelectOptionDict(value="INFO", label="INFO (обычный)"),
                            selector.SelectOptionDict(value="WARNING", label="WARNING (только предупреждения)"),
                            selector.SelectOptionDict(value="ERROR", label="ERROR (только ошибки)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
        )