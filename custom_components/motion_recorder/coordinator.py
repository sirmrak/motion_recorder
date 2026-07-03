"""Coordinator for Motion Recorder."""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_state_change_event, async_call_later, async_track_time_change
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
    DEFAULT_RETENTION_DAYS,
    STATE_IDLE,
    STATE_DETECTING,
    STATE_RECORDING,
    STATE_DELAYING,
    STATE_FINALIZING,
    STATE_COMPLETED,
    STATE_ERROR,
    STATE_DISABLED,
)

try:
    from homeassistant.components.stream.const import RECORDER_PROVIDER as _RECORDER_PROVIDER
except ImportError:
    _RECORDER_PROVIDER = "recorder"

_MIN_RECORDING_SIZE = 1024

# Глобальный логгер для всего модуля
_LOGGER = logging.getLogger(__name__)


def _get_config_value(entry, key, default=None):
    """Get config value from options first, then from data, then default."""
    return entry.options.get(key, entry.data.get(key, default))


def _mkdir_parents(p: Path):
    """Path.mkdir с keyword args для executor."""
    p.mkdir(parents=True, exist_ok=True)


class MotionRecorderCoordinator(DataUpdateCoordinator):
    """Motion Recorder coordinator."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"Motion Recorder {entry.data.get(CONF_CAMERA_ENTITY)}",
        )
        self.hass = hass
        self.entry = entry
        self.camera_entity = entry.data[CONF_CAMERA_ENTITY]
        
        # Читаем настройки из options с fallback на data
        self.save_path = _get_config_value(entry, CONF_SAVE_PATH, "{camera_name}")
        self.filename_template = _get_config_value(entry, CONF_FILENAME_TEMPLATE, "%d-%m-%Y_%H-%M-%S")
        self.max_duration = _get_config_value(entry, CONF_MAX_DURATION, 30)
        self.prebuffer = _get_config_value(entry, CONF_PREBUFFER, 0)
        self.motion_sensors = _get_config_value(entry, CONF_MOTION_SENSORS, [])
        self.motion_filter = _get_config_value(entry, CONF_MOTION_FILTER, 0)
        self.off_delay = _get_config_value(entry, CONF_OFF_DELAY, 0)
        self.force_stop_sensor = _get_config_value(entry, CONF_FORCE_STOP_SENSOR)
        self.force_stop_state = _get_config_value(entry, CONF_FORCE_STOP_STATE, "off")
        self.retention_days = _get_config_value(entry, CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        
        # State management
        self._state = STATE_IDLE
        self._attributes = {}
        
        # Enabled state
        self._enabled = True
        
        # Listeners
        self._unsub_motion_listeners = []
        self._unsub_force_stop_listener = None
        self._cleanup_unsub = None
        
        # Timers
        self._motion_detect_timer = None
        self._off_delay_timer = None
        
        # Recording
        self._current_stream = None
        self._current_record_task = None
        self._recording_start_time = None
        self._current_filename = None
        
        # Statistics
        self._recordings_count = 0
        self._total_duration = 0
        self._total_size = 0

    def _get_media_base_path(self) -> Path:
        """Получить базовый путь к медиа."""
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
        """Получить полную папку для сохранения записей."""
        camera_name = self.camera_entity.split(".")[1]
        save_path = self.save_path.format(camera_name=camera_name)
        
        if Path(save_path).is_absolute():
            return Path(save_path)
        
        return self._get_media_base_path() / save_path

    @property
    def state(self):
        """Return current state."""
        return self._state

    @property
    def attributes(self):
        """Return current attributes with statistics."""
        attrs = self._attributes.copy()
        attrs["recordings_count"] = self._recordings_count
        attrs["total_duration"] = self._total_duration
        attrs["total_size"] = self._total_size
        return attrs

    @property
    def is_enabled(self) -> bool:
        """Return if recording is enabled."""
        return self._enabled

    async def set_enabled(self, enabled: bool) -> None:
        """Set enabled state."""
        self._enabled = enabled
        _LOGGER.info("Recording %s", "enabled" if enabled else "disabled")
        
        if not enabled:
            # Останавливаем текущую запись, если она идёт
            if self._state == STATE_RECORDING:
                await self._stop_recording()
            # Переходим в состояние "disabled"
            await self._update_state(STATE_DISABLED)
        else:
            # Возвращаемся в idle (только если сейчас disabled)
            if self._state == STATE_DISABLED:
                await self._update_state(STATE_IDLE)

    async def async_setup(self):
        """Set up the coordinator."""
        _LOGGER.info("Setting up Motion Recorder coordinator for %s", self.camera_entity)
        _LOGGER.info("Settings: max_duration=%s, off_delay=%s, retention_days=%s", 
                    self.max_duration, self.off_delay, self.retention_days)
        await self._update_state(STATE_IDLE)
        await self._start_listening()
        
        # Запускаем ежедневную очистку в 3:00 ночи
        self._cleanup_unsub = async_track_time_change(
            self.hass,
            self._cleanup_old_recordings,
            hour=3,
            minute=0,
            second=0,
        )
        _LOGGER.info("Daily cleanup scheduled at 03:00")

    async def async_shutdown(self):
        """Shut down the coordinator."""
        _LOGGER.info("Shutting down Motion Recorder coordinator")
        await self._stop_listening()
        
        if self._cleanup_unsub:
            self._cleanup_unsub()
            self._cleanup_unsub = None
        
        if self._current_stream:
            await self._stop_recording()

    async def _update_state(self, state, **attributes):
        """Update state and attributes."""
        old_state = self._state
        self._state = state
        self._attributes.update(attributes)
        _LOGGER.debug("State changed: %s → %s", old_state, state)
        self.async_set_updated_data({
            "state": state,
            "attributes": self.attributes
        })

    async def _start_listening(self):
        """Start listening to motion sensors."""
        _LOGGER.info("Starting to listen to motion sensors: %s", self.motion_sensors)
        for sensor in self.motion_sensors:
            unsub = async_track_state_change_event(
                self.hass, [sensor], self._motion_state_changed
            )
            self._unsub_motion_listeners.append(unsub)
        
        if self.force_stop_sensor:
            _LOGGER.info("Force stop sensor: %s → %s", self.force_stop_sensor, self.force_stop_state)
            self._unsub_force_stop_listener = async_track_state_change_event(
                self.hass, [self.force_stop_sensor], self._force_stop_state_changed
            )

    async def _stop_listening(self):
        """Stop listening to sensors."""
        for unsub in self._unsub_motion_listeners:
            unsub()
        self._unsub_motion_listeners = []
        
        if self._unsub_force_stop_listener:
            self._unsub_force_stop_listener()
            self._unsub_force_stop_listener = None

    @callback
    def _motion_state_changed(self, event):
        """Handle motion sensor state change — ONLY on edges (fronts)."""
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        
        if not new_state:
            return
        
        sensor_entity = event.data.get("entity_id")
        old_s = old_state.state if old_state else None
        new_s = new_state.state
        
        # ИГНОРИРУЕМ события во время финализации
        if self._state in (STATE_FINALIZING, STATE_COMPLETED):
            _LOGGER.debug("Ignoring motion event during %s", self._state)
            return
        
        _LOGGER.debug("Motion event: %s %s → %s", sensor_entity, old_s, new_s)
        
        # ФРОНТ ВВЕРХ
        if new_s == "on" and old_s != "on":
            _LOGGER.debug("Motion FRONT UP: %s (%s → %s)", sensor_entity, old_s, new_s)
            self.hass.async_create_task(self._handle_motion_detected(sensor_entity))
        
        # ФРОНТ ВНИЗ
        elif old_s == "on" and new_s != "on":
            _LOGGER.debug("Motion FRONT DOWN: %s (%s → %s)", sensor_entity, old_s, new_s)
            self.hass.async_create_task(self._handle_motion_stopped())

    @callback
    def _force_stop_state_changed(self, event):
        """Handle force stop sensor state change."""
        new_state = event.data.get("new_state")
        if not new_state:
            return
        
        if new_state.state == self.force_stop_state:
            _LOGGER.info("Force stop triggered by %s", event.data.get("entity_id"))
            self.hass.async_create_task(self._force_stop_recording())

    def _is_any_motion_active(self) -> bool:
        """Check if any motion sensor is currently in 'on' state."""
        for sensor in self.motion_sensors:
            state = self.hass.states.get(sensor)
            if state and state.state == "on":
                return True
        return False

    async def _handle_motion_detected(self, triggered_by):
        """Handle motion detection with filter."""
        if not self._enabled:
            _LOGGER.debug("Recording disabled, ignoring motion")
            return
        
        _LOGGER.debug("Motion detected by %s, current state: %s", triggered_by, self._state)
        
        if self._state == STATE_RECORDING:
            _LOGGER.debug("Already recording, updating last motion time")
            self._attributes["last_motion_time"] = dt_util.utcnow().isoformat()
            return
        
        if self._state == STATE_DETECTING:
            _LOGGER.debug("Already detecting, ignoring")
            return
        
        if self._state == STATE_DELAYING:
            _LOGGER.info("Motion returned during off-delay, cancelling delay and continuing recording")
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
                    self.hass,
                    self.motion_filter,
                    self._start_recording_after_filter
                )
            except Exception as err:
                _LOGGER.error("Failed to create motion filter timer: %s", err)
                await self._start_recording_after_filter(None)
        else:
            await self._start_recording_after_filter(None)

    async def _start_recording_after_filter(self, _=None):
        """Start recording after motion filter."""
        _LOGGER.debug("Starting recording after filter")
        self._motion_detect_timer = None
        triggered_by = self._attributes.get("triggered_by")
        await self._start_recording(triggered_by)

    async def _handle_motion_stopped(self):
        """Handle motion stopped with off-delay."""
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
                        self.hass,
                        self.off_delay,
                        self._stop_recording_after_delay
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
            await self._update_state(STATE_IDLE)

    async def _stop_recording_after_delay(self, _=None):
        """Stop recording after off-delay."""
        _LOGGER.debug("Off-delay timer expired")
        self._off_delay_timer = None
        
        if self._is_any_motion_active():
            _LOGGER.info("Motion is still active — continuing recording")
            await self._update_state(STATE_RECORDING)
            return
        
        _LOGGER.info("No motion detected, stopping recording")
        await self._stop_recording()

    async def _start_recording(self, triggered_by):
        """Start recording the camera."""
        try:
            # Проверяем, есть ли уже активная запись
            if self._current_stream:
                recorder_output = self._current_stream.outputs().get(_RECORDER_PROVIDER)
                if recorder_output is not None:
                    _LOGGER.warning("Recording already in progress, ignoring new request")
                    return
            
            self._recording_start_time = dt_util.utcnow()
            
            await self._update_state(
                STATE_RECORDING,
                triggered_by=triggered_by,
                recording_start_time=self._recording_start_time.isoformat()
            )
            
            component = self.hass.data.get("camera")
            if not component:
                raise Exception("Camera component not loaded")
            
            camera = component.get_entity(self.camera_entity)
            if not camera:
                raise Exception(f"Camera {self.camera_entity} not found")
            
            stream = await camera.async_create_stream()
            if not stream:
                raise Exception(f"Could not create stream for {self.camera_entity}")
            
            self._current_stream = stream
            
            save_dir = self._get_save_dir()
            await self.hass.async_add_executor_job(_mkdir_parents, save_dir)
            
            timestamp = datetime.now().strftime(self.filename_template)
            filename = save_dir / f"{timestamp}.mp4"
            self._current_filename = str(filename)
            
            # INFO: Начало записии
            _LOGGER.info(
                "Recording started: %s (duration=%ds, lookback=%ds)",
                filename, self.max_duration, self.prebuffer
            )
            
            # Запускаем запись
            self._current_record_task = asyncio.create_task(
                stream.async_record(
                    video_path=str(filename),
                    duration=self.max_duration,
                    lookback=self.prebuffer,
                )
            )
            
            # Ждём завершения
            await self._current_record_task
            
            await self._finalize_recording(str(filename))
            
        except Exception as err:
            _LOGGER.error("Error starting recording: %s", err, exc_info=True)
            await self._update_state(STATE_ERROR, error_message=str(err))
            await asyncio.sleep(5)
            await self._update_state(STATE_IDLE)

    async def _stop_recording(self):
        """Stop current recording."""
        _LOGGER.debug("Stop recording called, current_stream: %s", self._current_stream is not None)
        
        if not self._current_stream:
            _LOGGER.warning("No current stream, nothing to stop")
            return
        
        try:
            recorder_output = self._current_stream.outputs().get(_RECORDER_PROVIDER)
            if recorder_output:
                _LOGGER.debug("Removing recorder provider to stop recording")
                await self._current_stream.remove_provider(recorder_output)
                
                # Ждём завершения задачи записии
                if self._current_record_task and not self._current_record_task.done():
                    _LOGGER.debug("Waiting for record task to complete after stop signal")
                    try:
                        await asyncio.wait_for(self._current_record_task, timeout=10.0)
                        _LOGGER.debug("Record task completed after stop")
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Record task did not complete within timeout, cancelling")
                        self._current_record_task.cancel()
                        try:
                            await self._current_record_task
                        except asyncio.CancelledError:
                            pass
                
                _LOGGER.debug("Recording stopped successfully")
            else:
                _LOGGER.warning("No recorder output found")
            
        except Exception as err:
            _LOGGER.error("Error stopping recording: %s", err, exc_info=True)
            await self._update_state(STATE_ERROR, error_message=str(err))
            await asyncio.sleep(5)
            if not self._enabled:
                await self._update_state(STATE_DISABLED)
            else:
                await self._update_state(STATE_IDLE)

    async def _force_stop_recording(self):
        """Force stop recording."""
        _LOGGER.info("Force stop recording called")
        await self._stop_recording()

    async def _finalize_recording(self, filename):
        """Finalize recording and check file."""
        _LOGGER.debug("Finalizing recording: %s", filename)
        
        # Переходим в finalizing
        await self._update_state(STATE_FINALIZING, last_file_path=filename)
        
        filepath = Path(filename)
        
        # Ждём файл максимум 2 секунды с шагом 0.2с
        for _ in range(10):
            await asyncio.sleep(0.2)
            if filepath.is_file():
                size = filepath.stat().st_size
                if size > _MIN_RECORDING_SIZE:
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
            
            # INFO: Конец записии
            _LOGGER.info(
                "Recording completed: %s (%.1f sec, %d bytes). Total: %d recordings, %d sec, %d bytes",
                filepath, duration, size,
                self._recordings_count, self._total_duration, self._total_size
            )
        else:
            _LOGGER.warning("Recording file not found or too small: %s", filename)
        
        # Очищаем ресурсы
        self._current_stream = None
        self._current_record_task = None
        self._recording_start_time = None
        self._current_filename = None
        
    async def _finalize_recording(self, filename):
        """Finalize recording and check file."""
        _LOGGER.debug("Finalizing recording: %s", filename)
        
        # Переходим в finalizing
        await self._update_state(STATE_FINALIZING, last_file_path=filename)
        
        filepath = Path(filename)
        
        # Ждём файл максимум 2 секунды с шагом 0.2с
        for _ in range(10):
            await asyncio.sleep(0.2)
            if filepath.is_file():
                size = filepath.stat().st_size
                if size > _MIN_RECORDING_SIZE:
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
            
            # INFO: Конец записии
            _LOGGER.info(
                "Recording completed: %s (%.1f sec, %d bytes). Total: %d recordings, %d sec, %d bytes",
                filepath, duration, size,
                self._recordings_count, self._total_duration, self._total_size
            )
        else:
            _LOGGER.warning("Recording file not found or too small: %s", filename)
        
        # Очищаем ресурсы
        self._current_stream = None
        self._current_record_task = None
        self._recording_start_time = None
        self._current_filename = None
        
        if not self._enabled:
            _LOGGER.debug("Recording disabled during finalization, transitioning to disabled")
            await self._update_state(STATE_DISABLED)
        else:
            _LOGGER.debug("Transitioning to idle")
            await self._update_state(STATE_IDLE)

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
        
        cutoff_time = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0
        deleted_size = 0
        
        def _cleanup():
            nonlocal deleted_count, deleted_size
            try:
                for file_path in save_dir.glob("*.mp4"):
                    if file_path.stat().st_mtime < cutoff_time.timestamp():
                        size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        deleted_size += size
                        _LOGGER.debug("Deleted old recording: %s", file_path.name)
            except Exception as err:
                _LOGGER.error("Error during cleanup: %s", err)
        
        await self.hass.async_add_executor_job(_cleanup)
        
        if deleted_count > 0:
            _LOGGER.info(
                "Cleanup completed: deleted %d files (%.1f MB)",
                deleted_count,
                deleted_size / (1024 * 1024)
            )
        else:
            _LOGGER.debug("No old recordings found to delete")