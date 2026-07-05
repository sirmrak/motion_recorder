# Motion Recorder for Home Assistant
English | [Русский](README.ru.md)

## A powerful Home Assistant integration for recording camera streams based on motion detection with advanced filtering and automated management.

## Features

-  **Motion-based recording** - Automatically record cameras when motion is detected
-  **Multiple motion sensors** - Support for multiple motion sensors per camera
- ️ **Smart timing controls**:
  - Motion filter (0-10s) - Prevent false triggers
  - Off-delay (0-60s) - Continue recording after motion stops
  - Maximum duration (10-3600s) - Auto-stop long recordings
-  **Pre-buffer support** - Include seconds before motion detection (0-60s)
-  **Flexible file management**:
  - Custom save paths with variables
  - Multiple filename templates
  - Automatic cleanup of old recordings (0-365 days)
-  **Force stop sensor** - Stop recording based on any entity state
-  **Enable/disable switch** - Control recording with a toggle
-  **Status sensor** - Monitor recording state with beautiful icons
-  **Smart device control** - Automatically turn on/off lights or switches based on recording state
-  **Auto-cleanup** - Delete old recordings by retention policy (daily at 03:00)
-  **Multi-language support** - English and Russian translations
-  **Hot reload** - Settings apply immediately without restart

## Installation

### Manual Installation

1. Download the `motion_recorder` folder
2. Place it in your Home Assistant `custom_components` directory:
   ```
   /config/custom_components/motion_recorder/
   ```
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration**
5. Search for **Motion Recorder** and follow the setup wizard

### Via HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations**
3. Click **⋮** (three dots) → **Custom repositories**
4. Add repository: `https://github.com/sirmrak/motion_recorder`
5. Category: **Integration**
6. Install **Motion Recorder** from HACS
7. Restart Home Assistant
8. Add integration via **Settings → Devices & Services**

## Configuration

### Initial Setup

When adding the integration, you'll configure:

| Setting | Description | Default | Range |
|---------|-------------|---------|-------|
| **Integration Name** | Display name for this instance | Motion Recorder | - |
| **Camera** | Camera entity to record | - | - |
| **Save Path** | Folder relative to `/media` | `{camera_name}` | - |
| **Filename Format** | Date/time format for files | `DD-MM-YYYY_HH-MM-SS` | - |
| **Max Duration** | Maximum recording length | 30 sec | 10-3600 sec |
| **Prebuffer** | Seconds before motion to include | 0 sec | 0-60 sec |
| **Motion Sensors** | Binary sensors to trigger recording | - | - |
| **Motion Filter** | Delay before starting (false trigger protection) | 0 sec | 0-10 sec |
| **Off Delay** | Wait after motion stops before stopping | 0 sec | 0-60 sec |
| **Force Stop Sensor** | Entity to force stop recording | - | - |
| **Force Stop State** | State value to trigger stop | `off` | - |
| **Retention Days** | Auto-delete recordings older than | 7 days | 0-365 days |
| **Controlled Devices** | Lights/switches to auto-control | - | - |
| **Control States** | States when devices should be ON | `recording` | Multi-select |
| **Log Level** | Logging verbosity | INFO | - |

### Options (Post-Setup)

After initial setup, you can modify these settings via **Settings → Devices & Services → Motion Recorder → Configure**:

- Maximum duration
- Prebuffer
- Motion filter
- Off delay
- Retention days
- Controlled Devices
- Control States
- Log level

Changes apply immediately without restart.

## Usage

### Basic Workflow

1. **Motion detected** → Sensor triggers
2. **Motion filter** (optional) → Wait to confirm it's not a false trigger
3. **Recording starts** → Camera stream saved to file
4. **Motion continues** → Recording continues up to max duration
5. **Motion stops** → Off-delay timer starts
6. **Off-delay expires** → Recording stops and file finalizes
7. **Cleanup** → Old files deleted based on retention policy

### State Machine

The integration uses a clear state machine visible in the status sensor:

```
idle → detecting → recording → delaying → finalizing → idle
```

| State | Icon | Description |
|-------|------|-------------|
| `idle` | `mdi:sleep` | Waiting for motion |
| `detecting` | `mdi:motion-sensor` | Motion filter active |
| `recording` | `mdi:record-rec` | Recording in progress |
| `delaying` | `mdi:timer-sand` | Waiting after motion stopped |
| `finalizing` | `mdi:file-check` | Saving and finalizing file |
| `disabled` | `mdi:video-off` | Recording disabled via switch |
| `error` | `mdi:alert-circle` | Error occurred |

### Entities Created

For each integration instance:

1. **Sensor** - `sensor.motion_recorder_<name>_status`
   - Shows current recording state
   - Attributes: recording count, total duration, total size, last file path

2. **Switch** - `switch.motion_recorder_<name>_enabled`
   - Enable/disable recording
   - State persists across restarts

### Automation Examples

#### Send notification when recording completes

```yaml
automation:
  - alias: "Notify on recording complete"
    trigger:
      - platform: state
        entity_id: sensor.motion_recorder_front_door_status
        to: "idle"
        from: "finalizing"
    action:
      - service: notify.mobile_app
        data:
          message: "Recording completed: {{ state_attr('sensor.motion_recorder_front_door_status', 'last_file_path') }}"
```

#### Turn on light during recording

```yaml
automation:
  - alias: "Light on during recording"
    trigger:
      - platform: state
        entity_id: sensor.motion_recorder_front_door_status
        to: "recording"
    action:
      - service: light.turn_on
        target:
          entity_id: light.porch
```

#### Disable recording at night

```yaml
automation:
  - alias: "Disable recording at night"
    trigger:
      - platform: time
        at: "23:00:00"
    action:
      - service: switch.turn_off
        target:
          entity_id: switch.motion_recorder_front_door_enabled

  - alias: "Enable recording in morning"
    trigger:
      - platform: time
        at: "07:00:00"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.motion_recorder_front_door_enabled
```

## File Management

### Save Path

The save path is relative to your Home Assistant `/media` directory. You can use variables:

- `{camera_name}` - Replaced with camera entity name (without `camera.` prefix)

**Examples:**
- `{camera_name}` → `/media/front_door/`
- `cameras/{camera_name}` → `/media/cameras/front_door/`
- `/absolute/path/recordings` → Absolute path (must exist)

### Filename Templates

Choose from predefined formats:

| Format | Example Output |
|--------|----------------|
| `DD-MM-YYYY_HH-MM-SS` | `02-07-2026_20-30-45.mp4` |
| `YYYY-MM-DD_HH-MM-SS` | `2026-07-02_20-30-45.mp4` |
| `DD.MM.YYYY_HH-MM-SS` | `02.07.2026_20-30-45.mp4` |
| `YYYYMMDD_HHMMSS` | `20260702_203045.mp4` |
| `DD.MM.YYYY_HH.MM.SS` | `02.07.2026_20.30.45.mp4` |

All formats include seconds to prevent file overwriting when multiple recordings occur in the same minute.

### Automatic Cleanup

Files older than the retention period are automatically deleted daily at 03:00.

- **0 days** = Never delete
- **7 days** = Delete files older than 7 days
- **30 days** = Delete files older than 30 days
- **365 days** = Delete files older than 1 year

Cleanup runs in the background and doesn't affect recording performance.

## Smart Device Control

Motion Recorder can automatically control lights and switches based on its current state.

### How It Works

1. Select one or more `light` or `switch` entities in settings
2. Choose which states should trigger the devices (e.g., `recording`, `delaying`)
3. When the integration enters a selected state → devices turn ON
4. When it leaves that state → devices turn OFF (only if integration turned them on)

### Safety Features

- **Non-intrusive**: If you manually turn on a light, the integration won't turn it off
- **Manual override respected**: If you turn off a controlled light during recording, it stays off
- **Shutdown cleanup**: All integration-controlled devices are turned off when HA restarts or integration reloads

### Example Use Cases

- Turn on porch light only while recording is active
- Activate alarm indicator LED during motion detection
- Turn on indoor lighting when camera detects movement at night

## Advanced Features

### Multiple Motion Sensors

You can select multiple motion sensors. Recording triggers when **any** sensor detects motion. The integration uses OR logic:

- Sensor A triggers → Recording starts
- Sensor A stops, Sensor B still active → Recording continues
- All sensors stop → Off-delay timer starts

### Force Stop Sensor

Use any entity (`binary_sensor`, `switch`, `input_boolean`) to force stop recording immediately:

- **Use case**: Stop recording when alarm is disarmed
- **Use case**: Stop recording when person leaves the room
- **Use case**: Emergency stop button

### Pre-buffer

Include seconds **before** motion detection in the recording:

- Requires active camera stream
- Actual lookback may be less than requested depending on stream buffer
- Useful for capturing what happened before motion triggered

## Troubleshooting

### Recording doesn't start

1. Check motion sensor is working in Home Assistant
2. Verify camera supports streaming
3. Check integration is enabled (switch is ON)
4. Review logs: **Settings → System → Logs** → filter by `motion_recorder`

### Files not created

1. Verify save path exists or can be created
2. Check Home Assistant has write permissions to `/media`
3. Review logs for permission errors
4. Try absolute path instead of relative

### Recording doesn't stop

1. Check off-delay setting (increase if needed)
2. Verify motion sensor actually stops triggering
3. Check max duration is set appropriately
4. Review logs for timer issues

### Status sensor shows "error"

1. Check logs for detailed error message
2. Verify camera entity is valid
3. Check stream component is working
4. Try restarting Home Assistant

### Logs

Set log level to **DEBUG** for detailed troubleshooting:

```yaml
logger:
  logs:
    custom_components.motion_recorder: DEBUG
```

## Requirements

- Home Assistant 2024.6 or later
- Camera with streaming support
- `stream` component enabled
- FFmpeg installed (for some camera types)

## Support

- **Issues**: [GitHub Issues](https://github.com/sirmrak/motion_recorder/issues)
- **Discussions**: [GitHub Discussions](https://github.com/sirmrak/motion_recorder/discussions)
- **Documentation**: [Home Assistant Documentation](https://www.home-assistant.io/integrations/)

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Credits

- Built with ❤️ for the Home Assistant community
- Inspired by various camera recording solutions
- Thanks to all contributors and testers

---

**Version**: 1.1.0  
**Last Updated**: 2026-07-05  
**Compatible with**: Home Assistant 2024.6+
