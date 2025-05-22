# Scene Sequencer

A Home Assistant custom component that cycles through a series of scenes sequentially, advancing with each activation. Includes an optional timeout feature that jumps to the final scene if activated after a specified period of inactivity.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/0x3333/scene_sequencer.svg)](https://github.com/0x3333/scene_sequencer/releases)

## Features

- Cycle through scenes in a predetermined sequence
- Track position for multiple independent sequences
- Jump to a final scene if activated after a specified timeout
- If after the timeout the current state of lights matches the last scene state, go to first automatically.

## Installation

### HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ → Custom repositories
   - Add URL `https://github.com/0x3333/scene_sequencer`
   - Select category: Integration
3. Click "Download"
4. Restart Home Assistant

### Manual Installation

1. Copy the `scene_sequencer` from the `custom_components` folder to your `custom_components` folder in your 
Home Assistant configuration directory
2. Restart Home Assistant

## Configuration

No configuration is needed in `configuration.yaml`. The component registers services automatically.

## Services

### scene_sequencer.cycle

Activates the next scene in a sequence.

| Parameter | Type | Required | Description |
| --------- | ---- | -------- | ----------- |
| `scenes` | list | Yes | List of scene entity IDs to cycle through in order |
| `go_to_last_timeout` | integer | No | Timeout period in seconds. If the service is invoked after this duration has elapsed since the last activation, the component will skip directly to the final scene in the sequence. This creates an effective 'shutdown' or 'reset' behavior when returning after a period of inactivity. When omitted, the sequence progresses normally through each scene. |

## Usage Examples

### Basic Scene Cycling

```yaml
# Example: Toggle between three lighting scenes
automation:
  - alias: "Living Room Scene Toggle"
    trigger:
      - platform: state
        entity_id: binary_sensor.living_room_switch
        to: "on"
    action:
      - service: scene_sequencer.cycle
        data:
          scenes:
            - scene.living_room_bright
            - scene.living_room_movie
            - scene.living_room_evening
```

### Multiple scenes with Turn off

```yaml
# Example: Cycle through relaxing scenes with turn off after timeout
automation:
  - alias: "Evening Wind-Down"
    trigger:
      - platform: input_button.press
        entity_id: input_button.wind_down
    action:
      - service: scene_sequencer.cycle
        data:
          scenes:
            - scene.evening_start
            - scene.dim_relax
            - scene.night_mood
            - scene.all_off
          go_to_last_timeout: 5  # seconds
```

## How It Works

The component maintains state for each unique scene sequence, tracking:
- Current position in the sequence
- Timestamp of last usage

Each time you call the `cycle` service with the same scene list, it advances to the next scene. The component uses a hashed identifier for each unique scene sequence to maintain independent tracking of multiple sequences.

If configured with `go_to_last_timeout`, if called `cycle` after the specified timeout, it will jump to the last scene. This is usefull if you have wall switches, so after activating a scene, the next press will go to the "turn_off" scene that must be the last one. If it goes to the last one, it will check if the current lights state matches the last scene state, which will make it go to the first one, so the user doesn't need to press twice in the wall switches.

## Entities

The component creates this entity:

- `binary_sensor.scene_sequencer_store`: Stores sequence data in attributes

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.