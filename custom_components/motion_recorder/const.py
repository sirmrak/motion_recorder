"""Constants for Motion Recorder integration."""
DOMAIN = "motion_recorder"

# Configuration keys
CONF_CAMERA_ENTITY = "camera_entity"
CONF_SAVE_PATH = "save_path"
CONF_FILENAME_TEMPLATE = "filename_template"
CONF_MAX_DURATION = "max_duration"
CONF_PREBUFFER = "prebuffer"
CONF_MOTION_SENSORS = "motion_sensors"
CONF_MOTION_FILTER = "motion_filter"
CONF_OFF_DELAY = "off_delay"
CONF_FORCE_STOP_SENSOR = "force_stop_sensor"
CONF_FORCE_STOP_STATE = "force_stop_state"
CONF_LOG_LEVEL = "log_level"
CONF_RETENTION_DAYS = "retention_days" 

# Defaults
DEFAULT_NAME = "Motion Recorder"
DEFAULT_SAVE_PATH = "{camera_name}"
DEFAULT_FILENAME_TEMPLATE = "%d-%m-%Y_%H-%M-%S"
DEFAULT_MAX_DURATION = 60
DEFAULT_PREBUFFER = 0
DEFAULT_MOTION_FILTER = 0
DEFAULT_OFF_DELAY = 10
DEFAULT_FORCE_STOP_STATE = "off"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_RETENTION_DAYS = 7

# Sensor states
STATE_IDLE = "idle"
STATE_DETECTING = "detecting"
STATE_RECORDING = "recording"
STATE_DELAYING = "delaying"
STATE_FINALIZING = "finalizing"
STATE_COMPLETED = "completed"
STATE_ERROR = "error"
STATE_DISABLED = "disabled" 