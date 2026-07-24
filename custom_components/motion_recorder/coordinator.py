"""Coordinator for Motion Recorder."""
import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_call_later,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

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
    CONF_RETENTION_DAYS,
    CONF_CONTROLLED_ENTITIES,
    CONF_CONTROL_STATES,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_CONTROL_STATES,
    STATE_IDLE,
    STATE_DETECTING,
    STATE_RECORDING,
    STATE_DELAYING,
    STATE_FINALIZING,
    STATE_ERROR,
    STATE_DISABLED,
)

try:
    from homeassistant.components.stream.const import RECORDER_PROVIDER as _RECORDER_PROVIDER
except ImportError:
    _RECORDER_PROVIDER = "recorder"

# Порог валидности файла записи (байт)
_MIN_RECORDING_SIZE = 1024

# Таймауты и параметры вынесены из тела методов для удобства тюнинга
_STREAM_CREATE_TIMEOUT = 30.0   # Создание стрима (камера может быть недоступна)
_RECORD_STOP_TIMEOUT = 10.0     # Ожидание штатного завершения задачи записи
_TASK_CANCEL_TIMEOUT = 5.0      # Ожидание после cancel зависшей задачи
_ERROR_RECOVERY_DELAY = 5       # Пауза перед возвратом из состояния error
_FINALIZE_POLL_STEP = 0.2       # Шаг опроса файла при финализации
_FINALIZE_POLL_TRIES = 10       # Макс. число попыток (итого ~2 сек)
_CLEANUP_HOUR = 3               # Час ежедневной очистки

_LOGGER = logging.getLogger(__name__)


def _get_config_value(entry, key, default=None):
    """Значение настройки: сначала options, потом data, потом default."""
    return entry.options.get(key, entry.data.get(key, default))


def _mkdir_parents(p: Path):
    """Path.mkdir с keyword-аргументами для executor."""
    p.mkdir(parents=True, exist_ok=True)


class MotionRecorderCoordinator(DataUpdateCoordinator):
    """Координатор Motion Recorder."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Инициализация координатора."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Motion Recorder {entry.data.get(CONF_CAMERA_ENTITY)}",
        )
        self.hass = hass
        self.entry = entry
        self.camera_entity = entry.data[CONF_CAMERA_ENTITY]

        # Настройки: options с fallback на data
        self.save_path = _get_config_value(entry, CONF_SAVE_PATH, "{camera_name}")
        self.filename_template = _get_config_value(
            entry, CONF_FILENAME_TEMPLATE, "%d-%m-%Y_%H-%M-%S"
        )
        self.max_duration = _get_config_value(entry, CONF_MAX_DURATION, 30)
        self.prebuffer = _get_config_value(entry, CONF_PREBUFFER, 0)
        self.motion_sensors = _get_config_value(entry, CONF_MOTION_SENSORS, [])
        self.motion_filter = _get_config_value(entry, CONF_MOTION_FILTER, 0)
        self.off_delay = _get_config_value(entry, CONF_OFF_DELAY, 0)
        self.force_stop_sensor = _get_config_value(entry, CONF_FORCE_STOP_SENSOR)
        self.force_stop_state = _get_config_value(entry, CONF_FORCE_STOP_STATE, "off")
        self.retention_days = _get_config_value(
            entry, CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        )

        # Управляемые устройства
        self.controlled_entities = _get_config_value(entry, CONF_CONTROLLED_ENTITIES, [])
        self.control_states = _get_config_value(
            entry, CONF_CONTROL_STATES, DEFAULT_CONTROL_STATES
        )
        self._we_turned_on: set[str] = set()
        # Кэш логического флага «устройства должны быть включены»,
        # чтобы не опрашивать состояния при каждом переходе состояния
        self._control_should_be_on: bool | None = None

        # Состояние
        self._state = STATE_IDLE
        self._attributes: dict = {}

        # Флаги разрешения/блокировки записи
        self._enabled = True
        self._force_stopped = False

        # Подписки
        self._unsub_motion_listeners: list = []
        self._unsub_force_stop_listener = None
        self._cleanup_unsub = None

        # Таймеры
        self._motion_detect_timer = None
        self._off_delay_timer = None

        # Текущая запись
        self._current_stream = None
        self._current_record_task = None
        self._recording_start_time = None
        self._current_filename = None

        # Статистика
        self._recordings_count = 0
        self._total_duration = 0
        self._total_size = 0

    # === Пути ===

    def _get_media_base_path(self) -> Path:
        """Базовый путь к медиа."""
        media_dirs = self.hass.config.media_dirs
        if media_dirs:
            if "media" in media_dirs:
                return Path(media_dirs["media"])
            if "local" in media_dirs:
                return Path(media_dirs["local"])
            first_key = next(iter(media_dirs))
            return Path(media_dirs[first_key])
        return Path(self.hass.config.path("media"))

    def _get_save_dir(self) -> Path:
        """Полная папка для сохранения записей."""
        camera_name = self.camera_entity.split(".")[1]
        save_path = self.save_path.format(camera_name=camera_name)
        if Path(save_path).is_absolute():
            return Path(save_path)
        return self._get_media_base_path() / save_path

    # === Свойства состояния ===

    @property
    def state(self):
        """Текущее состояние."""
        return self._state

    @property
    def attributes(self):
        """Атрибуты со статистикой."""
        attrs = self._attributes.copy()
        attrs["recordings_count"] = self._recordings_count
        attrs["total_duration"] = self._total_duration
        attrs["total_size"] = self._total_size
        attrs["force_stopped"] = self._force_stopped
        return attrs

    @property
    def is_enabled(self) -> bool:
        """Разрешена ли запись."""
        return self._enabled

    # === Хелперы блокировок ===

    def _is_blocked(self) -> bool:
        """Единая проверка причин запрета записи.

        Запись запрещена, если интеграция выключена переключателем
        либо активирована блокировка force_stop.
        """
        return (not self._enabled) or self._force_stopped

    def _idle_or_disabled_state(self) -> str:
        """«Спокойное» состояние с учётом блокировок."""
        return STATE_DISABLED if self._is_blocked() else STATE_IDLE

    async def _force_disable(self, reason: str) -> None:
        """Жёсткая остановка всего и переход в DISABLED.

        Единая точка для выключения переключателем и активации force_stop:
        отменяет таймеры, останавливает запись, гасит зависшие задачи,
        обнуляет стрим. Идемпотентна — повторный вызов безопасен.
        """
        _LOGGER.info("Force disable: %s", reason)
        await self._cancel_all_timers()

        # Останавливаем запись в активной фазе (recording или delaying)
        if self._state in (STATE_RECORDING, STATE_DELAYING):
            await self._stop_recording()

        # Гасим задачу, которая могла зависнуть на создании стрима/записи
        if self._current_record_task and not self._current_record_task.done():
            _LOGGER.info("Cancelling active record task (%s)", reason)
            self._current_record_task.cancel()
            try:
                await asyncio.wait_for(
                    self._current_record_task, timeout=_TASK_CANCEL_TIMEOUT
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._current_record_task = None

        self._current_stream = None
        await self._update_state(STATE_DISABLED)

    # === Включение/выключение ===

    async def set_enabled(self, enabled: bool) -> None:
        """Переключить разрешение записи."""
        self._enabled = enabled
        _LOGGER.info("Recording %s", "enabled" if enabled else "disabled")

        if not enabled:
            await self._force_disable("switch turned off")
        elif self._force_stopped:
            _LOGGER.info("Cannot enable: force stop is active")
            await self._update_state(STATE_DISABLED)
        else:
            await self._update_state(STATE_IDLE)

    async def _cancel_all_timers(self) -> None:
        """Отменить все активные таймеры."""
        if self._motion_detect_timer:
            _LOGGER.debug("Cancelling motion detect timer")
            self._motion_detect_timer()
            self._motion_detect_timer = None
        if self._off_delay_timer:
            _LOGGER.debug("Cancelling off-delay timer")
            self._off_delay_timer()
            self._off_delay_timer = None

    # === Жизненный цикл ===

    async def async_setup(self):
        """Запуск координатора."""
        _LOGGER.info("Setting up Motion Recorder coordinator for %s", self.camera_entity)
        _LOGGER.info(
            "Settings: max_duration=%s, off_delay=%s, retention_days=%s, "
            "controlled_entities=%s, control_states=%s",
            self.max_duration, self.off_delay, self.retention_days,
            self.controlled_entities, self.control_states,
        )
        await self._update_state(STATE_IDLE)
        await self._start_listening()

        # Ежедневная очистка в _CLEANUP_HOUR:00
        self._cleanup_unsub = async_track_time_change(
            self.hass,
            self._cleanup_old_recordings,
            hour=_CLEANUP_HOUR,
            minute=0,
            second=0,
        )
        _LOGGER.info("Daily cleanup scheduled at %02d:00", _CLEANUP_HOUR)

    async def async_shutdown(self):
        """Остановка координатора."""
        _LOGGER.info("Shutting down Motion Recorder coordinator")
        await self._stop_listening()

        if self._cleanup_unsub:
            self._cleanup_unsub()
            self._cleanup_unsub = None

        # Гасим устройства, которые включали сами
        for entity_id in list(self._we_turned_on):
            try:
                domain = entity_id.split(".")[0]
                _LOGGER.info("Shutdown: turning off %s", entity_id)
                await self.hass.services.async_call(
                    domain, "turn_off", {"entity_id": entity_id}, blocking=False,
                )
            except Exception as err:
                _LOGGER.error("Error turning off %s on shutdown: %s", entity_id, err)
        self._we_turned_on.clear()

        if self._current_stream:
            await self._stop_recording()

    # === Публикация состояния и управление устройствами ===

    async def _update_state(self, state, **attributes):
        """Обновить состояние и атрибуты, опубликовать данные."""
        old_state = self._state
        self._state = state
        self._attributes.update(attributes)
        _LOGGER.debug("State changed: %s → %s", old_state, state)
        self.async_set_updated_data({
            "state": state,
            "attributes": self.attributes,
        })

        # Управляем устройствами только при реальной смене состояния
        if old_state != state:
            await self._update_controlled_entities()

    async def _update_controlled_entities(self) -> None:
        """Синхронизировать управляемые устройства с состоянием.

        Реагирует только на изменение логического флага should_be_on,
        чтобы не опрашивать состояния сущностей при каждом переходе.
        """
        if not self.controlled_entities:
            return

        should_be_on = self._state in self.control_states
        if should_be_on == self._control_should_be_on:
            return
        self._control_should_be_on = should_be_on

        for entity_id in self.controlled_entities:
            try:
                domain = entity_id.split(".")[0]
                current = self.hass.states.get(entity_id)
                if current is None:
                    _LOGGER.warning("Controlled entity %s not found, skipping", entity_id)
                    continue

                is_on = current.state == "on"
                if should_be_on and not is_on:
                    _LOGGER.info("Turning ON %s (state=%s)", entity_id, self._state)
                    await self.hass.services.async_call(
                        domain, "turn_on", {"entity_id": entity_id}, blocking=False,
                    )
                    self._we_turned_on.add(entity_id)
                elif not should_be_on and is_on and entity_id in self._we_turned_on:
                    _LOGGER.info("Turning OFF %s (state=%s)", entity_id, self._state)
                    await self.hass.services.async_call(
                        domain, "turn_off", {"entity_id": entity_id}, blocking=False,
                    )
                    self._we_turned_on.discard(entity_id)
            except Exception as err:
                _LOGGER.error("Error controlling %s: %s", entity_id, err, exc_info=True)

    # === Очистка старых записей ===

    async def _cleanup_old_recordings(self, now=None):
        """Удалить записи старше retention_days."""
        if self.retention_days == 0:
            _LOGGER.debug("Retention days is 0, skipping cleanup")
            return

        save_dir = self._get_save_dir()
        if not save_dir.exists():
            _LOGGER.debug("Save directory does not exist: %s", save_dir)
            return

        _LOGGER.info("Starting cleanup of recordings older than %d days", self.retention_days)
        cutoff_ts = (dt_util.now() - timedelta(days=self.retention_days)).timestamp()
        deleted_count = 0
        deleted_size = 0

        def _cleanup():
            nonlocal deleted_count, deleted_size
            try:
                for file_path in save_dir.glob("*.mp4"):
                    st = file_path.stat()  # один stat на файл
                    if st.st_mtime < cutoff_ts:
                        file_path.unlink()
                        deleted_count += 1
                        deleted_size += st.st_size
                        _LOGGER.debug("Deleted old recording: %s", file_path.name)
            except Exception as err:
                _LOGGER.error("Error during cleanup: %s", err)

        await self.hass.async_add_executor_job(_cleanup)

        if deleted_count > 0:
            _LOGGER.info(
                "Cleanup completed: deleted %d files (%.1f MB)",
                deleted_count, deleted_size / (1024 * 1024),
            )
        else:
            _LOGGER.debug("No old recordings found to delete")

    # === Подписки на сенсоры ===

    async def _start_listening(self):
        """Подписаться на сенсоры движения и force_stop."""
        _LOGGER.info("Starting to listen to motion sensors: %s", self.motion_sensors)
        for sensor in self.motion_sensors:
            unsub = async_track_state_change_event(
                self.hass, [sensor], self._motion_state_changed
            )
            self._unsub_motion_listeners.append(unsub)

        if self.force_stop_sensor:
            _LOGGER.info(
                "Force stop sensor: %s → %s",
                self.force_stop_sensor, self.force_stop_state,
            )
            self._unsub_force_stop_listener = async_track_state_change_event(
                self.hass, [self.force_stop_sensor], self._force_stop_state_changed
            )

    async def _stop_listening(self):
        """Отписаться от сенсоров."""
        for unsub in self._unsub_motion_listeners:
            unsub()
        self._unsub_motion_listeners = []

        if self._unsub_force_stop_listener:
            self._unsub_force_stop_listener()
            self._unsub_force_stop_listener = None

    @callback
    def _motion_state_changed(self, event):
        """Обработка смены состояния сенсора движения — только фронты."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if not new_state:
            return

        # Не плодим задачи, пока запись запрещена или идёт финализация
        if self._is_blocked():
            return
        if self._state == STATE_FINALIZING:
            _LOGGER.debug("Ignoring motion event during %s", self._state)
            return

        sensor_entity = event.data.get("entity_id")
        old_s = old_state.state if old_state else None
        new_s = new_state.state
        _LOGGER.debug("Motion event: %s %s → %s", sensor_entity, old_s, new_s)

        if new_s == "on" and old_s != "on":
            _LOGGER.debug("Motion FRONT UP: %s (%s → %s)", sensor_entity, old_s, new_s)
            self.hass.async_create_task(self._handle_motion_detected(sensor_entity))
        elif old_s == "on" and new_s != "on":
            _LOGGER.debug("Motion FRONT DOWN: %s (%s → %s)", sensor_entity, old_s, new_s)
            self.hass.async_create_task(self._handle_motion_stopped())

    @callback
    def _force_stop_state_changed(self, event):
        """Обработка смены состояния сенсора force_stop."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        new_s = new_state.state
        sensor_entity = event.data.get("entity_id")
        _LOGGER.debug("Force stop sensor %s changed to: %s", sensor_entity, new_s)

        if new_s == self.force_stop_state:
            _LOGGER.info("Force stop ACTIVATED by %s", sensor_entity)
            self.hass.async_create_task(self._activate_force_stop())
        else:
            _LOGGER.info("Force stop DEACTIVATED by %s", sensor_entity)
            self.hass.async_create_task(self._deactivate_force_stop())

    async def _activate_force_stop(self) -> None:
        """Активировать блокировку force_stop."""
        self._force_stopped = True
        await self._force_disable("force stop sensor")

    async def _deactivate_force_stop(self) -> None:
        """Снять блокировку force_stop."""
        self._force_stopped = False
        _LOGGER.info("Force stop blocking deactivated")
        await self._update_state(self._idle_or_disabled_state())

    # === Обработчики движения ===

    def _is_any_motion_active(self) -> bool:
        """Активен ли сейчас хоть один сенсор движения."""
        for sensor in self.motion_sensors:
            state = self.hass.states.get(sensor)
            if state and state.state == "on":
                return True
        return False

    async def _handle_motion_detected(self, triggered_by):
        """Обработка обнаружения движения с учётом фильтра."""
        if self._is_blocked():
            _LOGGER.debug("Recording blocked, ignoring motion")
            return

        _LOGGER.debug("Motion detected by %s, current state: %s", triggered_by, self._state)

        if self._state == STATE_RECORDING:
            _LOGGER.debug("Already recording, updating last motion time")
            # Публикуем атрибут сразу, чтобы UI не «отставал»
            await self._update_state(
                STATE_RECORDING,
                last_motion_time=dt_util.utcnow().isoformat(),
            )
            return

        if self._state == STATE_DETECTING:
            _LOGGER.debug("Already detecting, ignoring")
            return

        if self._state == STATE_DELAYING:
            _LOGGER.info("Motion returned during off-delay, continuing recording")
            if self._off_delay_timer:
                _LOGGER.debug("Cancelling off-delay timer")
                self._off_delay_timer()
                self._off_delay_timer = None
            await self._update_state(STATE_RECORDING)
            return

        if self._off_delay_timer:
            _LOGGER.debug("Cancelling off-delay timer")
            self._off_delay_timer()
            self._off_delay_timer = None

        await self._update_state(STATE_DETECTING, triggered_by=triggered_by)

        if self.motion_filter > 0:
            _LOGGER.debug("Starting motion filter timer for %d seconds", self.motion_filter)
            try:
                self._motion_detect_timer = async_call_later(
                    self.hass, self.motion_filter, self._start_recording_after_filter
                )
            except Exception as err:
                _LOGGER.error("Failed to create motion filter timer: %s", err)
                await self._start_recording_after_filter(None)
        else:
            await self._start_recording_after_filter(None)

    async def _start_recording_after_filter(self, _=None):
        """Старт записи после отработки фильтра."""
        self._motion_detect_timer = None
        if self._is_blocked():
            _LOGGER.debug("Cannot start recording after filter: blocked")
            await self._update_state(self._idle_or_disabled_state())
            return

        _LOGGER.debug("Starting recording after filter")
        triggered_by = self._attributes.get("triggered_by")
        await self._start_recording(triggered_by)

    async def _handle_motion_stopped(self):
        """Обработка прекращения движения с off-delay."""
        if self._is_blocked():
            _LOGGER.debug("Recording blocked, ignoring motion stopped")
            return

        _LOGGER.debug("Motion stopped, current state: %s", self._state)

        if self._is_any_motion_active():
            _LOGGER.debug("Some motion sensor still active, ignoring stop")
            return

        _LOGGER.debug("All motion sensors are off")

        if self._state == STATE_RECORDING:
            _LOGGER.debug("Transitioning to DELAYING state")
            await self._update_state(STATE_DELAYING)

            if self._off_delay_timer:
                _LOGGER.debug("Cancelling old off-delay timer")
                self._off_delay_timer()
                self._off_delay_timer = None

            if self.off_delay > 0:
                _LOGGER.debug("Starting off-delay timer for %d seconds", self.off_delay)
                try:
                    self._off_delay_timer = async_call_later(
                        self.hass, self.off_delay, self._stop_recording_after_delay
                    )
                except Exception as err:
                    _LOGGER.error("Failed to create off-delay timer: %s", err)
                    await self._stop_recording_after_delay(None)
            else:
                _LOGGER.debug("No off-delay, stopping immediately")
                await self._stop_recording_after_delay(None)

        elif self._state == STATE_DETECTING:
            if self._motion_detect_timer:
                _LOGGER.debug("Cancelling motion filter timer")
                self._motion_detect_timer()
                self._motion_detect_timer = None
            await self._update_state(self._idle_or_disabled_state())

    async def _stop_recording_after_delay(self, _=None):
        """Остановка записи по истечении off-delay."""
        self._off_delay_timer = None
        if self._is_blocked():
            _LOGGER.debug("Recording blocked, skip stop after delay")
            return

        _LOGGER.debug("Off-delay timer expired")

        if self._is_any_motion_active():
            _LOGGER.info("Motion is still active — continuing recording")
            await self._update_state(STATE_RECORDING)
            return

        _LOGGER.info("No motion detected, stopping recording")
        await self._stop_recording()

    # === Запись ===

    async def _start_recording(self, triggered_by):
        """Запустить запись камеры."""
        if self._is_blocked():
            _LOGGER.warning("Cannot start recording: blocked")
            await self._update_state(self._idle_or_disabled_state())
            return

        filename = None
        try:
            if self._current_stream:
                recorder_output = self._current_stream.outputs().get(_RECORDER_PROVIDER)
                if recorder_output is not None:
                    _LOGGER.warning("Recording already in progress, ignoring new request")
                    return

            self._recording_start_time = dt_util.utcnow()
            await self._update_state(
                STATE_RECORDING,
                triggered_by=triggered_by,
                recording_start_time=self._recording_start_time.isoformat(),
            )

            component = self.hass.data.get("camera")
            if not component:
                raise Exception("Camera component not loaded")

            camera = component.get_entity(self.camera_entity)
            if not camera:
                raise Exception(f"Camera {self.camera_entity} not found")

            # Таймаут на создание стрима: камера может быть недоступна
            _LOGGER.debug("Creating stream for %s (timeout=%.0fs)",
                          self.camera_entity, _STREAM_CREATE_TIMEOUT)
            try:
                stream = await asyncio.wait_for(
                    camera.async_create_stream(), timeout=_STREAM_CREATE_TIMEOUT
                )
            except asyncio.TimeoutError as err:
                raise Exception(
                    f"Timeout creating stream for {self.camera_entity} "
                    f"— camera may be unavailable"
                ) from err

            if not stream:
                raise Exception(f"Could not create stream for {self.camera_entity}")

            self._current_stream = stream

            save_dir = self._get_save_dir()
            await self.hass.async_add_executor_job(_mkdir_parents, save_dir)

            timestamp = dt_util.now().strftime(self.filename_template)
            filename = save_dir / f"{timestamp}.mp4"
            self._current_filename = str(filename)

            _LOGGER.info(
                "Recording started: %s (duration=%ds, lookback=%ds)",
                filename, self.max_duration, self.prebuffer,
            )

            self._current_record_task = asyncio.create_task(
                stream.async_record(
                    video_path=str(filename),
                    duration=self.max_duration,
                    lookback=self.prebuffer,
                )
            )

            # Ждём завершения с корректной обработкой отмены
            try:
                await self._current_record_task
            except asyncio.CancelledError:
                _LOGGER.info("Recording task was cancelled (stop requested)")

            await self._finalize_recording(str(filename))

        except asyncio.CancelledError:
            _LOGGER.info("Recording start was cancelled")
            if filename:
                await self._finalize_recording(str(filename))
        except Exception as err:
            _LOGGER.error("Error starting recording: %s", err, exc_info=True)
            # При блокировке не мигаем error — сразу в спокойное состояние
            if self._is_blocked():
                await self._update_state(self._idle_or_disabled_state())
                return
            await self._update_state(STATE_ERROR, error_message=str(err))
            await asyncio.sleep(_ERROR_RECOVERY_DELAY)
            await self._update_state(self._idle_or_disabled_state())

    async def _stop_recording(self):
        """Остановить текущую запись."""
        _LOGGER.debug(
            "Stop recording called, stream=%s, task=%s",
            self._current_stream is not None,
            self._current_record_task is not None,
        )

        if not self._current_stream:
            _LOGGER.warning("No current stream, nothing to stop")
            return

        try:
            recorder_output = self._current_stream.outputs().get(_RECORDER_PROVIDER)
            if recorder_output:
                _LOGGER.debug("Removing recorder provider to stop recording")
                await self._current_stream.remove_provider(recorder_output)

                if self._current_record_task and not self._current_record_task.done():
                    _LOGGER.debug("Waiting for record task (timeout=%.0fs)", _RECORD_STOP_TIMEOUT)
                    try:
                        await asyncio.wait_for(
                            self._current_record_task, timeout=_RECORD_STOP_TIMEOUT
                        )
                        _LOGGER.debug("Record task completed after stop")
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Record task did not complete, cancelling")
                        self._current_record_task.cancel()
                        try:
                            await asyncio.wait_for(
                                self._current_record_task, timeout=_TASK_CANCEL_TIMEOUT
                            )
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            _LOGGER.warning("Record task cancel timed out")
                    except asyncio.CancelledError:
                        _LOGGER.debug("Record task was cancelled")

                _LOGGER.debug("Recording stopped successfully")
            else:
                _LOGGER.warning("No recorder output found")

        except Exception as err:
            _LOGGER.error("Error stopping recording: %s", err, exc_info=True)
            # Принудительная отмена зависшей задачи при любой ошибке
            if self._current_record_task and not self._current_record_task.done():
                self._current_record_task.cancel()
            await self._update_state(STATE_ERROR, error_message=str(err))
            await asyncio.sleep(_ERROR_RECOVERY_DELAY)
            await self._update_state(self._idle_or_disabled_state())

    async def _force_stop_recording(self):
        """Принудительная остановка записи (legacy-точка входа)."""
        _LOGGER.info("Force stop recording called")
        await self._stop_recording()

    async def _finalize_recording(self, filename):
        """Финализация записи и проверка файла."""
        _LOGGER.debug("Finalizing recording: %s", filename)
        await self._update_state(STATE_FINALIZING, last_file_path=filename)

        filepath = Path(filename)
        for _ in range(_FINALIZE_POLL_TRIES):
            await asyncio.sleep(_FINALIZE_POLL_STEP)
            if filepath.is_file():
                if filepath.stat().st_size > _MIN_RECORDING_SIZE:
                    break

        if filepath.is_file() and filepath.stat().st_size > _MIN_RECORDING_SIZE:
            size = filepath.stat().st_size
            if self._recording_start_time:
                duration = (dt_util.utcnow() - self._recording_start_time).total_seconds()
            else:
                duration = self.max_duration

            self._recordings_count += 1
            self._total_duration += int(duration)
            self._total_size += size

            _LOGGER.info(
                "Recording completed: %s (%.1f sec, %d bytes). "
                "Total: %d recordings, %d sec, %d bytes",
                filepath, duration, size,
                self._recordings_count, self._total_duration, self._total_size,
            )
        else:
            _LOGGER.warning("Recording file not found or too small: %s", filename)

        # Освобождаем ресурсы
        self._current_stream = None
        self._current_record_task = None
        self._recording_start_time = None
        self._current_filename = None

        if self._is_blocked():
            _LOGGER.debug("Blocked during finalization, transitioning to disabled")
            await self._update_state(STATE_DISABLED)
        else:
            _LOGGER.debug("Transitioning to idle")
            await self._update_state(STATE_IDLE)